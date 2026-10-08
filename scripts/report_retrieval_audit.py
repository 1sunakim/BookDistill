"""Render audit artifacts without changing labels or production configuration."""
from __future__ import annotations
import argparse
import html
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    args=parser.parse_args(); out=args.directory
    read=lambda name:json.loads((out/name).read_text(encoding='utf-8'))
    metrics=read('comparison.json'); inputs=read('input-audit.json')
    live=read('live-index-audit.json'); model=read('model-artifact-manifest.json')
    differences=read('per-question-differences.json')
    top6=read('default-top6-and-truncation.json')
    thresholds=json.loads((Path(__file__).resolve().parents[1]/'examples/retrieval_release_thresholds.v1.json').read_text(encoding='utf-8'))
    rows=[]
    for name,m in metrics.items():
        rows.append(f"| {name} | {m['recall_at_10']:.3f} | {m['mrr_at_10']:.3f} | {m['ndcg_at_10']:.3f} | {m['short_chunk_hit_rate']:.1%} | {m['p95_ms']:.1f} | {m['degraded_queries']} |")
    groups=[]
    for name in ('isolated-hashing','bge-lexical','bge-semantic'):
        for tag,g in metrics[name]['groups'].items():
            groups.append(f"| {name} | {tag} | {g['cases']} | {g['recall_at_10']:.3f} | {g['mrr_at_10']:.3f} | {g['ndcg_at_10']:.3f} |")
    comparisons=[]
    for d in differences:
        comparisons.append(f"| {d['case_id']} | {d['question']} | {d['recall_delta']:+.3f} | {d['mrr_delta']:+.3f} | {d['ndcg_delta']:+.3f} | {len(d['added'])} / {len(d['removed'])} |")
    new=metrics['bge-semantic']
    gates={
        'Recall@10':new['recall_at_10']>=thresholds['minimum_recall_at_10'],
        'MRR@10':new['mrr_at_10']>=thresholds['minimum_mrr'],
        'nDCG@10':new['ndcg_at_10']>=thresholds['minimum_ndcg_at_10'],
        'P95':new['p95_ms']<=thresholds['maximum_latency_p95_ms'],
        'stage_degradation':new['degraded_queries']==0,
    }
    (out/'acceptance.json').write_text(json.dumps({'numeric_gates':gates,'human_gold_verified':False,'production_acceptance':False,'reason':'Existing annotations are codex-manual; independent human review is unverified. Numerical failures also block release.'},ensure_ascii=False,indent=2),encoding='utf-8')
    report='''# BookDistill 模块二检索质量审计 — 2026-09-28

本次只读检查生产容器，并在独立本地 Qdrant 目录重建测试索引。未修改 `.env`、生产 collection、用户数据或生成相关性阈值；未调用 DeepSeek 生成。

## 实际生效状态

API 与 Worker 环境均选择 hashing / token_overlap，向量维度 512。环境中的 BGE 模型名称未被这两个 provider 使用。当前生产 Dense/Sparse collection 均为 0 Point，在线检索返回 HTTP 409。因此没有当前有效 Payload 模型版本，也无法取得本次在线 Dense/Sparse/Rerank Trace；不能把历史运行记录冒充当前索引状态。证据见 `live-index-audit.json`、`worker-environment.json`、`live-query-error.json`。

本报告的旧配置为归档真实 PDF 的同配置重建基线，不是在线生产结果。健康接口返回正常并不能证明书籍已入索引。生产日志显示 2026-09-27 有检索请求和后续按书删除记录，但日志没有输出每次请求的候选分数、模型 Trace 或拒答 request_id；因此历史 DeepSeek 拒答无法逐请求还原。

## 语料、标注与计分口径

使用真实 PDF《深入理解 AI Agent》的 969 个归档 Chunk；原 PDF 与导出记录的 SHA-256 一致。10 题全部金标 ID 存在，书籍范围和标注页范围一致。完整输入指纹见 `input-audit.json`。

现有数据虽然写着 reviewed，但所有 annotator 都是 codex-manual-2026-09-27，未发现独立人工复核证明。保留原标签，不把模型标注称为人工金标。未列入相关集合的候选是 unjudged，计算二元指标时暂按 0 处理，不能据此断言人工判定不相关。人工复核入口为 `human-review.html`，每条候选可导出 0/1/2/无法判断标签。

Recall@10 为每题相关 Chunk 覆盖率再宏平均；MRR@10、nDCG@10 用相同二元标签。短 Chunk 定义固定为原 Chunk token_count < 32，不使用扩展后的文本长度。Top10 实验关闭上下文扩展，final_evidence=10，max_per_chapter=10，预算 32000，以免 Top6 或生成预算截断 Recall@10；Dense 30 / Sparse 30 / Fusion 40 / Rerank 30，其余排序设置不变。故本表不是默认 Top6 答案质量验收。完整 Trace、分数、正文、原 Chunk、章节及一基 PDF 页码逐题落盘。

## 旧新结果与消融

| 配置 | Recall@10 | MRR@10 | nDCG@10 | 短 Chunk 命中率 | P95 ms | 降级题数 |
|---|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(rows)+'''

`bge-lexical` 只换 Embedding；`bge-semantic` 同时使用 BGE cosine 语义重排。语义重排仍是同一个双塔 Embedding 的相似度，不是 bge-reranker-base CrossEncoder。Sparse 全程保持 Qdrant Sparse Vector + 现有稳定词项 Hash/log-TF + 服务端 IDF；代码类名带 BM25，但该编码器并未实现完整 BM25 文档长度归一化。旧新比较没有降低任何相关性阈值。

## 中英、同义和跨语言

| 配置 | 分组 | 题数 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---:|---:|---:|---:|
'''+ '\n'.join(groups)+'''

分组标签允许重叠，en 与 cross-language 是相同四题；不能当成八个独立样本。同义组仅一题，统计量不足以代表一般效果。中文 BGE 的官方定位为中文模型，跨语言能力必须以本次实测为准。

## 逐题差异（BGE 语义重排减 hashing）

| 问题 | 查询 | ΔRecall | ΔMRR | ΔnDCG | 新增 / 移除 Chunk 数 |
|---|---|---:|---:|---:|---:|
'''+ '\n'.join(comparisons)+'''

完整新旧 ID 顺序及差集见 `per-question-differences.json`；逐题页码、各阶段得分、正文及待人工相关性见 `*-top10.json` 与人工检查页。每份 `*-traces.json` 保留 Dense/Sparse/Rerank 状态及候选分数，`*-index-audit.json` 全量校验 969 个 ID、原 Chunk 内容、模型、向量长度和 Chunk 版本。

默认 Top 6 独立检查：hashing 方案的证据锚点中 1.67% 为短 Chunk，10 道问题都没有一次命中多个短锚点；BGE 语义重排方案本次为 0%。该金标问题集没有复现用户描述的“Top 6 多个极短片段”。另有 187/969 个 Chunk 的实际 BGE tokenizer 编码超过 512 tokens（最长 789），所以当前 FastEmbed 默认截断会损失部分 Chunk 尾部语义；见 `default-top6-and-truncation.json`。短片段仅作诊断，未用于过滤或抬高得分。

## 模型来源与索引迁移要求

FastEmbed 注册的逻辑模型为 BAAI/bge-small-zh-v1.5，ONNX 工件来自 Qdrant/bge-small-zh-v1.5。已在推理前检查本地工件存在、大小及 SHA-256，离线运行；来源、revision、工件校验值见 `model-artifact-manifest.json`。本地 SHA-256 用于复现，不等于已与远端签名校验。官方来源：[BAAI 模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)、[Qdrant ONNX 工件仓库](https://huggingface.co/Qdrant/bge-small-zh-v1.5)。

即使 hashing 与 BGE 都是 512 维，向量空间也不兼容。正式切换必须重新生成所有 Dense 向量，写入新 collection，不能仅改 provider 并复用原向量。Sparse 若编码器及 Chunk 不变可复用，但应校验 ID/checksum/Chunk 版本一一对应；本次实验为每个方案都新建 Dense/Sparse，验证内容完全一致。重排模型无持久化向量，但必须在运行清单记录实际类和模型名。现在的 Trace.model_names 仅从召回 Payload 汇总，不包含 Reranker；本次独立清单补齐，不能仅靠该字段声称验证了重排模型。

上线顺序：冻结原 Chunk 导出与模型工件 → 新建带模型版本的 collection → 批量重建并校验 ID/checksum/512 维/模型名 → 真实人工复核集过门槛 → API 与 Worker 同时切换 → 保留旧 collection 供回滚。当前不执行生产切换。

## 验收结论与限制

项目门槛为 Recall@10 ≥ 0.85、MRR ≥ 0.65、nDCG@10 ≥ 0.70、P95 ≤ 8000 ms。BGE 语义方案实测分别为 '''+f"{new['recall_at_10']:.3f} / {new['mrr_at_10']:.3f} / {new['ndcg_at_10']:.3f} / {new['p95_ms']:.1f} ms"+'''；三项质量分数未达门槛，P95 达标。数值检查见 `acceptance.json`；人工独立复核尚未完成，因此不能宣告真实人工集上线验收通过。

本次生产 collection 为空，无法还原历史那次 DeepSeek 拒答时的具体问题、Top6 与模型响应。安全拒答本身不能证明 DeepSeek 故障。后续请求应成组保留 request_id、原始查询、六条原 Chunk、完整 Trace 和拒答原因，才能归因。本次基线与新方案使用同一归档语料，可用于检索排序比较；Qdrant local mode 的延迟不可等同于 Docker HTTP/多并发生产延迟。
'''
    (out/'report.md').write_text(report,encoding='utf-8')
    data={name:read(name+'-top10.json') for name in ('isolated-hashing','bge-lexical','bge-semantic')}
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><meta charset="utf-8"><title>BookDistill 检索人工复核</title>
<style>body{font:16px system-ui;max-width:1300px;margin:30px auto;padding:20px;background:#f6f7fa}article{background:white;padding:18px;margin:16px 0;border:1px solid #ddd}pre{white-space:pre-wrap;line-height:1.6}small{color:#555}select,button{padding:8px}header{position:sticky;top:0;background:#f6f7fa;padding:10px}</style>
<h1>真实 PDF 检索人工复核</h1><p>已有标签来源为 codex-manual，未列入金标的候选标记为 unjudged。请对照原 PDF 独立判断；本页不自动覆盖金标。页码为 PDF 一基页码。</p>
<header><select id="variant"></select> <select id="question"></select> <button id="export">导出人工判断 JSON</button></header><main id="results"></main>
<script>const data=PAYLOAD;const answers={};const v=document.querySelector('#variant'),q=document.querySelector('#question'),r=document.querySelector('#results');for(const name of Object.keys(data))v.add(new Option(name,name));for(const c of data[v.value])q.add(new Option(c.case_id+' '+c.question,c.case_id));function render(){r.replaceChildren();const c=data[v.value].find(x=>x.case_id===q.value);for(const x of c.top10){const a=document.createElement('article');const title=document.createElement('h3');title.textContent='#'+x.rank+' · PDF '+x.pdf_pages_1based.join('–')+' · '+x.token_count+' Token';a.append(title);const meta=document.createElement('small');meta.textContent=x.chunk_id+' | '+x.chapter_path.join(' / ')+' | label='+x.label+' | Dense='+x.dense_score+' Sparse='+x.sparse_score+' Rerank='+x.rerank_score;a.append(meta);const p=document.createElement('pre');p.textContent=x.text;a.append(p);const s=document.createElement('select');for(const [val,label] of [['','待判断'],['0','不相关'],['1','部分相关'],['2','直接支持答案'],['unknown','无法判断']])s.add(new Option(label,val));const key=c.case_id+':'+x.chunk_id;s.value=answers[key]?.relevance??'';s.onchange=()=>answers[key]={case_id:c.case_id,chunk_id:x.chunk_id,pdf_pages_1based:x.pdf_pages_1based,relevance:s.value,reviewer:'',reviewed_at:new Date().toISOString()};a.append(s);r.append(a)}}v.onchange=render;q.onchange=render;document.querySelector('#export').onclick=()=>{const blob=new Blob([JSON.stringify(Object.values(answers),null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='human-relevance.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};render();</script>'''.replace('PAYLOAD',payload)
    (out/'human-review.html').write_text(page,encoding='utf-8')
    print(json.dumps(gates))


if __name__=='__main__': main()
