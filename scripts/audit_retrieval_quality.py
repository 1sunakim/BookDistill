"""Read-only live audit and isolated, reproducible retrieval comparison.

Never writes to the live API or production Qdrant. Test indexes live under a
new output directory. Existing human labels are preserved, never invented.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
from bookdistill.contracts import Chunk, RetrievalConfig, RetrievalRequest, RetrievalScope
from bookdistill.evaluation.metrics import recall_at_k, reciprocal_rank, ndcg_at_k, percentile


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def summarize(rows):
    answerable = [r for r in rows if r['answerable']]
    return {
        'cases': len(rows),
        **{k: sum(r[k] for r in answerable) / max(1, len(answerable)) for k in ('recall_at_10', 'mrr_at_10', 'ndcg_at_10')},
        'short_chunk_hit_rate': sum(sum(x['short'] for x in r['top10']) for r in rows) / max(1, sum(len(r['top10']) for r in rows)),
        'p95_ms': percentile([r['latency_ms'] for r in rows], .95),
        'degraded_queries': sum(r['degraded'] for r in rows),
    }


LIVE_SCRIPT = '''
import json, os, hashlib
from collections import Counter
from urllib.request import urlopen, Request
keys = ['DENSE_BACKEND','SPARSE_BACKEND','EMBEDDING_PROVIDER','EMBEDDING_MODEL','EMBEDDING_DIMENSION','RERANKER_PROVIDER','RERANKER_MODEL','QDRANT_URL','QDRANT_COLLECTION','QDRANT_SPARSE_COLLECTION','DENSE_TIMEOUT_MS','SPARSE_TIMEOUT_MS','RERANK_TIMEOUT_MS']
env = {k:os.getenv('BOOKDISTILL_'+k) for k in keys}
base = env['QDRANT_URL']
result = {'environment':env, 'collections':{}}
for name in [env['QDRANT_COLLECTION'], env['QDRANT_SPARSE_COLLECTION']]:
    info = json.load(urlopen(base+'/collections/'+name))['result']
    points=[]; offset=None
    while True:
        body={'limit':256,'with_payload':True,'with_vector':True}
        if offset is not None: body['offset']=offset
        req=Request(base+'/collections/'+name+'/points/scroll',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        page=json.load(urlopen(req))['result']; points.extend(page['points']); offset=page.get('next_page_offset')
        if offset is None: break
    result['collections'][name]={'config':info['config'], 'point_count':len(points),
        'models':dict(Counter(str(p['payload'].get('model_name')) for p in points)),
        'index_versions':dict(Counter(str(p['payload'].get('index_version')) for p in points)),
        'chunk_versions':dict(Counter(str(p['payload'].get('chunk_version')) for p in points)),
        'vector_dimensions':dict(Counter(str(len(p['vector'])) for p in points if isinstance(p.get('vector'),list))),
        'records':[{'id':p['id'],'payload':p['payload']} for p in points]}
print(json.dumps(result,ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--docker', type=Path)
    parser.add_argument('--compare', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    corpus_path = ROOT/'artifacts/retrieval-quality-20260927/real-pdf-corpus.json'
    dataset_path = ROOT/'examples/evaluation_dataset.ai-agents-real.manual-dev.v1.json'
    corpus = json.loads(corpus_path.read_text(encoding='utf-8'))
    dataset = json.loads(dataset_path.read_text(encoding='utf-8'))
    chunks = [Chunk.model_validate(c) for c in corpus['chunks']]
    by_id = {str(c.chunk_id):c for c in chunks}
    cases = dataset['cases']
    for c in cases:
        for cid in c['relevant_chunk_ids']:
            assert cid in by_id, (c['case_id'], cid)
            assert str(by_id[cid].book_id) in c['book_scope']
            assert any(p['page_start'] <= by_id[cid].page_end and p['page_end'] >= by_id[cid].page_start for p in c['relevant_page_ranges'])
    save(out/'input-audit.json', {
        'created_at':datetime.now(timezone.utc), 'corpus_sha256':digest(corpus_path),
        'dataset_sha256':digest(dataset_path), 'pdf_sha256':digest(ROOT/corpus['source_pdf']),
        'export_pdf_sha256':corpus['source_sha256'], 'chunks':len(chunks),
        'short_definition':'original Chunk token_count < 32; before context expansion',
        'short_chunks':sum(c.token_count<32 for c in chunks), 'cases':len(cases),
        'annotators':dict(Counter(c['annotator'] for c in cases)),
        'review_status':dict(Counter(c['review_status'] for c in cases)),
        'human_independent_review_confirmed':False,
        'label_note':'Existing labels are codex-manual; reviewed field alone is not independent human review. Unlisted chunks are unjudged, scored as 0 for benchmark only.',
    })
    if args.docker:
        proc = subprocess.run([str(args.docker), 'exec','-i','bookdistill-api-1','python','-'], input=LIVE_SCRIPT, text=True, encoding='utf-8', capture_output=True, check=True)
        live = json.loads(proc.stdout)
        save(out/'live-index-audit.json', live)
        proc = subprocess.run([str(args.docker),'exec','bookdistill-worker-1','python','-c', "import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith(('BOOKDISTILL_EMBEDDING','BOOKDISTILL_RERANK','BOOKDISTILL_DENSE','BOOKDISTILL_SPARSE','BOOKDISTILL_QDRANT'))}))"], capture_output=True, text=True, encoding='utf-8', check=True)
        save(out/'worker-environment.json', json.loads(proc.stdout))
    def evaluate(name, search, variant='hybrid_rerank'):
        rows=[]
        traces=[]
        for case in cases:
            # Top10 ranking experiment: prevent generation's Top6/context budget
            # from silently turning Recall@10 into Recall@6.
            req = RetrievalRequest(request_id=uuid4(),query=case['question'],scope=RetrievalScope(book_ids=case['book_scope'],chapter_ids=case['chapter_scope']),config=RetrievalConfig(variant=variant,final_evidence=10,max_per_chapter=10,context_window=0,context_token_budget=32000,max_context_tokens_per_evidence=8000))
            response = search(req)
            traces.append({'case_id':case['case_id'], 'response':response})
            ranked=[e['chunk_id'] for e in response['evidence']]
            relevant=set(case['relevant_chunk_ids'])
            items=[]
            for rank,e in enumerate(response['evidence'],1):
                original=by_id[e['chunk_id']]
                assert e['text']==original.text
                assert e['page_start']==original.page_start and e['page_end']==original.page_end
                items.append({'rank':rank,'chunk_id':e['chunk_id'],'chapter_id':str(original.chapter_id),'chapter_path':original.chapter_path,'pdf_pages_1based':[original.page_start+1,original.page_end+1],'token_count':original.token_count,'short':original.token_count<32,'label':'relevant' if e['chunk_id'] in relevant else 'unjudged','human_relevance':None,'label_author':case['annotator'],'dense_score':e['dense_score'],'sparse_score':e['sparse_score'],'fusion_score':e['fusion_score'],'rerank_score':e['rerank_score'],'text':original.text})
            trace=response['trace']
            rows.append({'case_id':case['case_id'],'question':case['question'],'tags':case['tags'],'answerable':case['is_answerable'],'top10':items,'recall_at_10':recall_at_k(ranked,relevant,10),'mrr_at_10':reciprocal_rank(ranked,relevant),'ndcg_at_10':ndcg_at_k(ranked,relevant,10),'latency_ms':trace['total_latency_ms'],'degraded':any(trace.get(k,False) for k in ('dense_degraded','sparse_degraded','rerank_degraded'))})
        save(out/(name+'-top10.json'),rows)
        save(out/(name+'-traces.json'),traces)
        result=summarize(rows)
        result['groups']={tag:summarize([r for r in rows if tag in r['tags']]) for tag in ('zh','en','synonym','cross-language','mixed-term') if any(tag in r['tags'] for r in rows)}
        print(name,json.dumps(result,ensure_ascii=False),flush=True)
        return rows,result

    if not args.compare:
        with httpx.Client(base_url='http://127.0.0.1:8000',timeout=60,trust_env=False) as client:
            def live_search(req):
                response=client.post('/api/v1/retrieval/search',json=req.model_dump(mode='json')); response.raise_for_status(); return response.json()
            try:
                rows,summary=evaluate('live-hashing',live_search)
            except httpx.HTTPStatusError as error:
                save(out/'live-query-error.json',{'status':error.response.status_code,'body':error.response.text,'note':'Live baseline unavailable; reconstruct configured hashing baseline in isolated index from archived corpus.'})
                print('Live query unavailable:',error.response.status_code,flush=True)
                return
            req=RetrievalRequest(request_id=uuid4(),query=cases[0]['question'],scope=RetrievalScope(book_ids=cases[0]['book_scope']))
            save(out/'live-default-top6.json',live_search(req))
            save(out/'live-summary.json',summary)
        return

    os.environ['HF_HUB_OFFLINE']='1'
    from fastembed import TextEmbedding
    from qdrant_client import QdrantClient
    from bookdistill.retrieval import FastEmbedEmbeddingProvider, EmbeddingSimilarityReranker, HybridRetrievalEngine, HashingEmbeddingProvider, TokenOverlapReranker, QdrantSparseIndex
    from bookdistill.retrieval.qdrant_index import QdrantDenseIndex
    cache=ROOT/'data/model-cache'
    model='BAAI/bge-small-zh-v1.5'
    description=next(x for x in TextEmbedding.list_supported_models() if x['model']==model)
    model_root=cache/'models--Qdrant--bge-small-zh-v1.5'
    files=[{'path':str(p.relative_to(model_root)),'bytes':p.stat().st_size,'sha256':digest(p)} for p in model_root.rglob('*') if p.is_file()]
    assert any(p['path'].endswith('model_optimized.onnx') and p['bytes']>90000000 for p in files)
    save(out/'model-artifact-manifest.json',{'fastembed_registry':description,'revision':(model_root/'refs/main').read_text().strip(),'files':files,'offline':True})
    semantic=FastEmbedEmbeddingProvider(model,cache_dir=cache,threads=4)
    semantic.embed_query('预热检索模型')
    results={}; all_rows={}
    for name,embedding,reranker in [('isolated-hashing',HashingEmbeddingProvider(512),TokenOverlapReranker()),('bge-lexical',semantic,TokenOverlapReranker()),('bge-semantic',semantic,EmbeddingSimilarityReranker(semantic))]:
        db=out/(name+'-qdrant')
        if db.exists(): raise RuntimeError('Use a fresh output directory; refusing to overwrite '+str(db))
        client=QdrantClient(path=str(db))
        dense=QdrantDenseIndex('unused','audit_dense',embedding.dimension,client=client)
        sparse=QdrantSparseIndex('unused','audit_sparse',client=client)
        engine=HybridRetrievalEngine(embedding=embedding,dense_index=dense,sparse_index=sparse,reranker=reranker,retrieval_version='audit-'+name)
        print('Indexing',name,len(chunks),flush=True)
        engine.index_chunks(chunks,{chunks[0].book_id:corpus['title']})
        # Validate every indexed payload and vector dimension, not only hits.
        manifests={}
        sets=[]
        for collection in ('audit_dense','audit_sparse'):
            points=[]; offset=None
            while True:
                batch,offset=client.scroll(collection,limit=256,offset=offset,with_payload=True,with_vectors=True)
                points.extend(batch)
                if offset is None: break
            ids={str(p.id) for p in points}; sets.append(ids)
            assert ids==set(by_id)
            for p in points:
                assert Chunk.model_validate(p.payload['chunk'])==by_id[str(p.id)]
                if collection=='audit_dense':
                    assert len(p.vector)==embedding.dimension
                    assert p.payload['model_name']==embedding.model_name
            manifests[collection]={'count':len(points),'models':dict(Counter(p.payload['model_name'] for p in points)),'chunk_versions':dict(Counter(p.payload['chunk_version'] for p in points)),'dimension':embedding.dimension if collection=='audit_dense' else None}
        assert sets[0]==sets[1]
        save(out/(name+'-index-audit.json'),{'indexes':manifests,'reranker':reranker.model_name,'content_matches_corpus':True})
        rows,result=evaluate(name,lambda req:engine.search(req).model_dump(mode='json'))
        all_rows[name]=rows; results[name]=result
        if name=='bge-semantic':
            for variant in ('dense','sparse','hybrid'):
                _,results['bge-'+variant]=evaluate('bge-'+variant,lambda req:engine.search(req).model_dump(mode='json'),variant)
        client.close()
    save(out/'comparison.json',results)
    differences=[]
    for old,new in zip(all_rows['isolated-hashing'],all_rows['bge-semantic']):
        old_ids=[x['chunk_id'] for x in old['top10']]; new_ids=[x['chunk_id'] for x in new['top10']]
        differences.append({'case_id':old['case_id'],'question':old['question'],'old_ids':old_ids,'new_ids':new_ids,'added':[x for x in new_ids if x not in old_ids],'removed':[x for x in old_ids if x not in new_ids],'recall_delta':new['recall_at_10']-old['recall_at_10'],'mrr_delta':new['mrr_at_10']-old['mrr_at_10'],'ndcg_delta':new['ndcg_at_10']-old['ndcg_at_10']})
    save(out/'per-question-differences.json',differences)


if __name__=='__main__':
    main()
