"""Lazy, optional Transformers provider for local causal language models."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

from bookdistill.contracts.generation import GenerationConfig, ModelUsage

from .errors import ProviderConfigurationError, ProviderRequestError
from .ports import ProviderResult


class TransformersLocalProvider:
    """Run a local Hugging Face model without leaking framework types.

    Heavy dependencies and model weights are loaded on the first request, so API
    startup and non-generation commands stay lightweight.
    """

    provider_name = "transformers-local"

    def __init__(
        self,
        *,
        model_path: Path,
        adapter_path: Path | None = None,
        device: str = "auto",
        dtype: str = "auto",
        max_input_tokens: int = 8192,
    ) -> None:
        if not model_path.is_dir():
            raise ProviderConfigurationError(
                f"Local model directory does not exist: {model_path}"
            )
        if adapter_path is not None and not adapter_path.is_dir():
            raise ProviderConfigurationError(
                f"Local adapter directory does not exist: {adapter_path}"
            )
        if device not in {"auto", "cuda", "cpu"}:
            raise ProviderConfigurationError(
                "BOOKDISTILL_LLM_LOCAL_DEVICE must be auto, cuda, or cpu."
            )
        if dtype not in {"auto", "float16", "bfloat16", "float32"}:
            raise ProviderConfigurationError(
                "BOOKDISTILL_LLM_LOCAL_DTYPE must be auto, float16, bfloat16, or float32."
            )
        if max_input_tokens < 256:
            raise ProviderConfigurationError(
                "BOOKDISTILL_LLM_LOCAL_MAX_INPUT_TOKENS must be at least 256."
            )
        self.model_path = model_path.resolve()
        self.adapter_path = adapter_path.resolve() if adapter_path else None
        self.device = device
        self.dtype = dtype
        self.max_input_tokens = max_input_tokens
        adapter_suffix = f"+{self.adapter_path.name}" if self.adapter_path else ""
        self.model_name = f"{self.model_path.name}{adapter_suffix}"
        self._model = None
        self._tokenizer = None
        self._torch = None
        self._load_lock = Lock()
        self._generation_lock = Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except ImportError as error:
                raise ProviderConfigurationError(
                    "Local LLM dependencies are missing. Install the project optional "
                    "dependency group 'local-llm'."
                ) from error

            selected_device = self.device
            if selected_device == "auto":
                selected_device = "cuda" if torch.cuda.is_available() else "cpu"
            if selected_device == "cuda" and not torch.cuda.is_available():
                raise ProviderConfigurationError(
                    "CUDA was requested but is not available to PyTorch."
                )

            if self.dtype == "auto":
                selected_dtype = torch.float16 if selected_device == "cuda" else torch.float32
            else:
                selected_dtype = getattr(torch, self.dtype)

            try:
                tokenizer = AutoTokenizer.from_pretrained(
                    self.model_path,
                    local_files_only=True,
                    trust_remote_code=False,
                )
                load_options: dict[str, object] = {
                    "local_files_only": True,
                    "trust_remote_code": False,
                    "dtype": selected_dtype,
                    "low_cpu_mem_usage": True,
                }
                if selected_device == "cuda":
                    load_options["device_map"] = "auto"
                model = AutoModelForCausalLM.from_pretrained(
                    self.model_path, **load_options
                )
                if selected_device == "cpu":
                    model.to("cpu")
                if self.adapter_path is not None:
                    try:
                        from peft import PeftModel
                    except ImportError as error:
                        raise ProviderConfigurationError(
                            "PEFT is required when BOOKDISTILL_LLM_LOCAL_ADAPTER_PATH is set."
                        ) from error
                    model = PeftModel.from_pretrained(
                        model, self.adapter_path, local_files_only=True
                    )
                model.eval()
            except ProviderConfigurationError:
                raise
            except Exception as error:
                raise ProviderConfigurationError(
                    f"Failed to load local model '{self.model_path}': {error}"
                ) from error

            self._torch = torch
            self._tokenizer = tokenizer
            self._model = model

    @staticmethod
    def _json_candidate(content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```json") and stripped.endswith("```"):
            stripped = stripped[7:-3].strip()
        elif stripped.startswith("```") and stripped.endswith("```"):
            stripped = stripped[3:-3].strip()
        if not stripped.startswith("{"):
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                stripped = stripped[start : end + 1]
        return stripped

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, object],
        generation_config: GenerationConfig,
    ) -> ProviderResult:
        self._load()
        assert self._model is not None
        assert self._tokenizer is not None
        assert self._torch is not None

        schema_text = json.dumps(output_schema, ensure_ascii=False, separators=(",", ":"))
        messages = [
            {
                "role": "system",
                "content": (
                    system_prompt
                    + "\nReturn one JSON object only. It must satisfy this JSON Schema:\n"
                    + schema_text
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        try:
            prompt = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = self._tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=self.max_input_tokens,
            )
            input_tokens = int(inputs["input_ids"].shape[-1])
            target_device = self._model.get_input_embeddings().weight.device
            inputs = {name: tensor.to(target_device) for name, tensor in inputs.items()}
            do_sample = generation_config.temperature > 0
            eos_ids = []
            if self._tokenizer.eos_token_id is not None:
                eos_ids.append(self._tokenizer.eos_token_id)
            im_end_id = self._tokenizer.convert_tokens_to_ids("<|im_end|>")
            if isinstance(im_end_id, int) and im_end_id >= 0 and im_end_id not in eos_ids:
                eos_ids.append(im_end_id)
            options: dict[str, object] = {
                "max_new_tokens": generation_config.max_output_tokens,
                "do_sample": do_sample,
                "eos_token_id": eos_ids or None,
                "pad_token_id": (
                    self._tokenizer.pad_token_id or self._tokenizer.eos_token_id
                ),
            }
            if do_sample:
                options["temperature"] = generation_config.temperature
            with self._generation_lock, self._torch.inference_mode():
                output = self._model.generate(**inputs, **options)
            generated = output[0, input_tokens:]
            output_tokens = int(generated.shape[-1])
            content = self._tokenizer.decode(generated, skip_special_tokens=True)
        except Exception as error:
            if self._torch.cuda.is_available() and "out of memory" in str(error).lower():
                self._torch.cuda.empty_cache()
            raise ProviderRequestError(
                f"Local model generation failed: {error}", retryable=False
            ) from error

        return ProviderResult(
            content=self._json_candidate(content),
            finish_reason=(
                "length"
                if output_tokens >= generation_config.max_output_tokens
                else "stop"
            ),
            request_id=f"local-{uuid4()}",
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )
