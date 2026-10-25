# Full-landscape analysis of Hugging Face datasets matching search "government"
# (389 datasets, fetched 2026-10-22 via the HF API with full=true).
# Produces the numbers behind plan/hf-gov/index.html. Classification is
# rule-based over id + description + tags; rules are printed with the results
# so the report's 口径 is reproducible.
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# 按脚本所在目录找数据，从仓库根目录运行也能复跑。
with open(Path(__file__).with_name('datasets.json'), encoding='utf-8') as f:
    DATA = json.load(f)
print(f'TOTAL: {len(DATA)}')

# ---------- basics ----------
def tags_of(d, prefix):
    return [t.split(':', 1)[1] for t in d.get('tags') or [] if t.startswith(prefix + ':')]

authors = Counter(d['author'] for d in DATA)
print(f'UNIQUE AUTHORS: {len(authors)}')
print('TOP AUTHORS BY COUNT:', authors.most_common(12))

dls = sorted((d.get('downloads') or 0 for d in DATA), reverse=True)
likes = sorted((d.get('likes') or 0 for d in DATA), reverse=True)
print(f'DOWNLOADS total={sum(dls)} median={dls[len(dls)//2]} zero={(sum(1 for x in dls if x==0))} <10={(sum(1 for x in dls if x<10))} top10_share={sum(dls[:10])/max(sum(dls),1):.1%}')
print(f'LIKES total={sum(likes)} median={likes[len(likes)//2]} zero={(sum(1 for x in likes if x==0))}')
print(f'GATED: {sum(1 for d in DATA if d.get("gated"))}  DISABLED: {sum(1 for d in DATA if d.get("disabled"))}')

years = Counter((d.get('createdAt') or '')[:4] for d in DATA)
print('CREATED BY YEAR:', dict(sorted(years.items())))
recent = sum(1 for d in DATA if (d.get('lastModified') or '') >= '2026-07-24')
print(f'LAST MODIFIED within ~90d of 2026-10-22: {recent}')

lic = Counter()
for d in DATA:
    ls = tags_of(d, 'license')
    lic[ls[0] if ls else '(none stated)'] += 1
print('LICENSES:', lic.most_common(12))

lang = Counter()
for d in DATA:
    for l in tags_of(d, 'language'):
        lang[l] += 1
print('LANGUAGES:', lang.most_common(12))

sizes = Counter()
for d in DATA:
    ss = tags_of(d, 'size_categories')
    sizes[ss[0] if ss else '(unknown)'] += 1
print('SIZES:', sizes.most_common(10))

tasks = Counter()
for d in DATA:
    for t in tags_of(d, 'task_categories'):
        tasks[t] += 1
print('TASK CATEGORIES:', tasks.most_common(12))

fmts = Counter()
for d in DATA:
    for t in tags_of(d, 'format'):
        fmts[t] += 1
print('FORMATS:', fmts.most_common(10))

# ---------- thematic classification ----------
FACTORY_AUTHORS = {'electricsheepafrica', 'electricsheepasia', 'electricsheepeurope'}
FACTORY_CATEGORY = '批量指标切片（工厂）'
CATS = [
    ('考试/基准题集', ['mmlu', 'mmmlu', 'benchmark', 'beir', 'mteb', 'bias-detection', 'natural instructions', 'exam', 'syntheticdocqa', 'high_school_government_and_politics', 'high-school-government-and-politics']),
    ('LLM 训练语料', ['corpus', 'crawling', 'pretrain', 'llm', 'training', 'open-government', 'open_government', 'parallel', 'dpo', 'embedding', 'doc-corpus', 'tokens', 'text-generation']),
    ('财政·合同·采购', ['contract', 'procurement', 'rfp', 'rfq', 'tender', 'budget', 'spending', 'expenditure', 'taxation', 'fiscal', 'treasury', 'securities', 'auction']),
    ('公共服务与民生计划', ['scheme', 'government_services', 'benefit', 'permit', 'transport', 'education', 'school', 'housing', 'social security']),
    ('法律·法规·政策文件', ['law', 'legal', 'legislation', 'ordinance', 'regulation', 'statute', 'judicial', 'directive', 'treaty', 'government-standards', 'auditing-standards', 'government-offices', 'government_offices']),
    ('目录·清单型（注册表）', ['catalog', 'registry', 'directory', 'government-github-repos', 'publications-urls']),
    ('统计·指标·指数', ['statistics', 'statistical', 'census', 'demograph', 'population', 'indicator', 'statistical index']),
    ('卫生·选举·环境等', ['health', 'covid', 'disease', 'hospital', 'medical', 'climate', 'environment', 'emission', 'weather', 'election', 'news', 'blogs']),
]
def classify(d):
    text = (d['id'] + ' ' + (d.get('description') or '')).lower()
    tg = ' '.join(d.get('tags') or []).lower()
    best, best_score = '其他/弱相关', 0
    for name, kws in CATS:
        score = sum(2 for k in kws if k in text) + sum(1 for k in kws if k in tg)
        if score > best_score:
            best, best_score = name, score
    return best

cat_of = {}
cat_members = defaultdict(list)
factory = [d for d in DATA if d['author'] in FACTORY_AUTHORS]
non_factory = [d for d in DATA if d['author'] not in FACTORY_AUTHORS]
cat_members[FACTORY_CATEGORY] = factory
cat_of.update((d['id'], FACTORY_CATEGORY) for d in factory)
print(f'\nFACTORY: {len(factory)}; NON-FACTORY: {len(non_factory)}')
print('CLASSIFICATION RULES: id + description: 2 points/keyword; tags: 1 point/keyword; first category wins ties; no match: 其他/弱相关')
print(f'  {FACTORY_CATEGORY}: author in {sorted(FACTORY_AUTHORS)} (excluded before keyword scoring)')
for name, kws in CATS:
    print(f'  {name}: {kws}')
for d in non_factory:
    c = classify(d)
    cat_of[d['id']] = c
    cat_members[c].append(d)
print('\nCATEGORIES:')
for c, members in sorted(cat_members.items(), key=lambda kv: -len(kv[1])):
    top = sorted(members, key=lambda d: -(d.get('downloads') or 0))[:3]
    print(f'  {c}: {len(members)}  (downloads sum {sum(d.get("downloads") or 0 for d in members)})  e.g. ' + ', '.join(f"{d['id']}({d.get('downloads') or 0})" for d in top))
print('\nNON-FACTORY CATEGORY MEMBERS:')
for d in non_factory:
    print(f"  {d['id']}: {cat_of[d['id']]}")

# ---------- top by downloads / likes ----------
print('\nTOP 15 BY DOWNLOADS:')
for d in sorted(DATA, key=lambda d: -(d.get('downloads') or 0))[:15]:
    print(f"  {d['id']} dl={d.get('downloads')} likes={d.get('likes')} lic={tags_of(d,'license')} :: {(d.get('description') or '')[:110]!r}")
print('\nTOP 10 BY LIKES:')
for d in sorted(DATA, key=lambda d: -(d.get('likes') or 0))[:10]:
    print(f"  {d['id']} likes={d.get('likes')} dl={d.get('downloads')} :: {(d.get('description') or '')[:110]!r}")

# ---------- datumoj prior-art probes ----------
print('\nCATALOG/REGISTRY-LIKE (id or description mentions catalog/registry/directory/portal of datasets):')
for d in DATA:
    text = (d['id'] + ' ' + (d.get('description') or '')).lower()
    if re.search(r'(catalog|registry|directory|index of|list of datasets|portal)', text):
        print(f"  {d['id']} dl={d.get('downloads')} :: {(d.get('description') or '')[:100]!r}")
print('\nCROSS-COUNTRY / GLOBAL angle:')
for d in DATA:
    text = (d['id'] + ' ' + (d.get('description') or '')).lower()
    if re.search(r'(global|worldwide|international|multi-country|cross-country|all countries|around the world)', text):
        print(f"  {d['id']} dl={d.get('downloads')} :: {(d.get('description') or '')[:100]!r}")

# mirror-bot style authors (orgs publishing many)
print('\nAUTHORS WITH >=4 DATASETS:')
for a, n in authors.most_common():
    if n >= 4:
        ex = next(d['id'] for d in DATA if d['author'] == a)
        tot = sum(d.get('downloads') or 0 for d in DATA if d['author'] == a)
        print(f'  {a}: {n} datasets, total dl {tot}, e.g. {ex}')
