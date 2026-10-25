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
CATS = [
    ('门户镜像与数据目录', ['data.gouv', 'data.gov', 'open data', 'opendata', 'catalog', 'portal', 'mirror', 'dataset directory', 'data portal', 'ckan', 'socrata']),
    ('法律·法规·判例', ['law', 'legal', 'legislation', 'court', 'constitution', 'regulation', 'statute', 'judicial', 'crime act', 'directive', 'treaty', 'bill text']),
    ('议会·选举·政治', ['parliament', 'congress', 'election', 'senate', 'hansard', 'politic', 'vote', 'legislat', 'deput', 'assembly']),
    ('财政·预算·税务·采购', ['budget', 'spending', 'expenditure', 'procurement', 'tax', 'fiscal', 'treasury', 'contract award', 'public finance']),
    ('人口·统计·普查', ['census', 'demograph', 'population', 'statistics', 'statistical', 'survey data']),
    ('公共卫生·医疗', ['health', 'covid', 'disease', 'hospital', 'medical', 'nhs', 'mortality', 'epidemi']),
    ('环境·气候·地理空间', ['climate', 'environment', 'emission', 'weather', 'geospatial', 'geo ', 'land use', 'pollution', 'energy']),
    ('政府文档语料（LLM/文本）', ['corpus', 'gazette', 'official document', 'government document', 'web crawl', 'pdf', 'documents', 'text collection', 'pretraining', 'llm']),
    ('公共服务·行政事务', ['service', 'benefit', 'permit', 'form', 'transport', 'education', 'school', 'housing', 'social security']),
    ('执法·安全·投诉', ['police', 'crime', 'complaint', 'robocall', 'fraud', 'enforcement', 'prison', 'firearm']),
    ('演讲·新闻·公报', ['speech', 'press release', 'news', 'announcement', 'statement', 'transcript']),
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
for d in DATA:
    c = classify(d)
    cat_of[d['id']] = c
    cat_members[c].append(d)
print('\nCATEGORIES:')
for c, members in sorted(cat_members.items(), key=lambda kv: -len(kv[1])):
    top = sorted(members, key=lambda d: -(d.get('downloads') or 0))[:3]
    print(f'  {c}: {len(members)}  (downloads sum {sum(d.get("downloads") or 0 for d in members)})  e.g. ' + ', '.join(f"{d['id']}({d.get('downloads') or 0})" for d in top))

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
