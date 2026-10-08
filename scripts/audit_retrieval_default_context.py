"""Inspect default Top6 and BGE token truncation using existing test indexes."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ['HF_HUB_OFFLINE']='1'

from qdrant_client import QdrantClient
from tokenizers import Tokenizer
from bookdistill.contracts import Chunk, RetrievalRequest, RetrievalScope
from bookdistill.retrieval import HybridRetrievalEngine, HashingEmbeddingProvider, FastEmbedEmbeddingProvider, EmbeddingSimilarityReranker, TokenOverlapReranker, QdrantSparseIndex
from bookdistill.retrieval.qdrant_index import QdrantDenseIndex


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('directory',type=Path); args=parser.parse_args(); out=args.directory
    corpus=json.loads((ROOT/'artifacts/retrieval-quality-20260927/real-pdf-corpus.json').read_text(encoding='utf-8'))
    cases=json.loads((ROOT/'examples/evaluation_dataset.ai-agents-real.manual-dev.v1.json').read_text(encoding='utf-8'))['cases']
    chunks={c['chunk_id']:Chunk.model_validate(c) for c in corpus['chunks']}
    tokenizer_path=next((ROOT/'data/model-cache/models--Qdrant--bge-small-zh-v1.5/snapshots').rglob('tokenizer.json'))
    tokenizer=Tokenizer.from_file(str(tokenizer_path)); tokenizer.no_truncation()
    lengths={cid:len(tokenizer.encode(c.text).ids) for cid,c in chunks.items()}
    result={'bge_input_tokens':{'chunks_over_512':sum(n>512 for n in lengths.values()),'max':max(lengths.values()),'note':'Chunk token_count uses regex-v1; BGE uses WordPiece with special tokens. Index keeps original text even when model truncates input.'},'default_top6':{}}
    semantic=FastEmbedEmbeddingProvider(cache_dir=ROOT/'data/model-cache',threads=4); semantic.embed_query('warmup')
    for name,embed,rerank in [('isolated-hashing',HashingEmbeddingProvider(512),TokenOverlapReranker()),('bge-semantic',semantic,EmbeddingSimilarityReranker(semantic))]:
        path=out/(name+'-qdrant')
        if not path.is_dir(): raise RuntimeError('Existing isolated index required')
        client=QdrantClient(path=str(path))
        assert client.collection_exists('audit_dense') and client.collection_exists('audit_sparse')
        engine=HybridRetrievalEngine(embedding=embed,dense_index=QdrantDenseIndex('unused','audit_dense',512,client=client),sparse_index=QdrantSparseIndex('unused','audit_sparse',client=client),reranker=rerank)
        rows=[]
        for case in cases:
            response=engine.search(RetrievalRequest(request_id=uuid4(),query=case['question'],scope=RetrievalScope(book_ids=case['book_scope'])))
            anchors=[chunks[str(e.chunk_id)] for e in response.evidence]
            segments=[]
            for e in response.evidence:
                for cid in e.context_chunk_ids:
                    c=chunks[str(cid)]
                    assert c.chapter_id==e.chapter_id and c.book_id==e.book_id
                    assert e.page_start<=c.page_start<=c.page_end<=e.page_end
                    segments.append({'evidence_id':str(e.evidence_id),'chunk_id':str(cid),'chapter_id':str(c.chapter_id),'pdf_pages_1based':[c.page_start+1,c.page_end+1],'original_text':c.text})
            rows.append({'case_id':case['case_id'],'short_anchor_count':sum(c.token_count<32 for c in anchors),'evidence_count':len(anchors),'response':response.model_dump(mode='json'),'source_segments':segments})
        result['default_top6'][name]={'short_anchor_rate':sum(r['short_anchor_count'] for r in rows)/sum(r['evidence_count'] for r in rows),'cases_with_multiple_short_anchors':sum(r['short_anchor_count']>=2 for r in rows),'cases':rows}
        client.close()
    (out/'default-top6-and-truncation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'bge_input_tokens':result['bge_input_tokens'],'default_top6':{k:{x:y for x,y in v.items() if x!='cases'} for k,v in result['default_top6'].items()}},ensure_ascii=False))


if __name__=='__main__': main()
