"""苏丹的游戏 自用 RAG：把配置转写成"人话"文档 + 人工经验种子，BM25 检索。

纯标准库：中文按单字 + 二字切分，名字加权；不依赖向量模型（智能应用控制不拦）。
语料：
  rite:<id>   仪式：地点、提示、每格条件、每个结算分支（条件 → 结果），⛔妻子的不满 ☠移除/结局
  event:<id>  事件：触发时机、条件、选项及各选项后果
  card:<id>   卡牌：属性标签、装备加成、在哪些格子能放、从哪获得、被哪里用到
  over:<id>   结局
  seed:<文件>#<序号>  人工经验（kb_seeds/*.md，按 ## 标题切块）
语料缓存在状态目录的 kb_cache.json.gz，配置/种子文件变化或 KB_VERSION 升级后自动重建。
"""

import json
import math
import local_state
import re
from collections import Counter, defaultdict
from pathlib import Path

from jsonc import base_key

KB_VERSION = '0.5.2'  # 升版本 = 强制重建索引

RARE = {1: '石', 2: '铜', 3: '银', 4: '金'}
TYPE = {'char': '人物', 'item': '物品', 'sudan': '苏丹卡'}
OPS = ('>=', '<=', '=', '>', '<')


class Humanizer:
    """把条件/动作 JSON 翻译成中文短句。"""

    def __init__(self, game):
        self.g = game
        self.cn = game.counter_names()
        self.over = game.merged('over')[0]

    def card(self, cid):
        try:
            name = self.g.card_cfg(cid).get('name')
        except Exception:
            name = None
        return f'{(name or str(cid)).splitlines()[0]}' if name else str(cid)

    def thing(self, x):
        s = str(x)
        if s.startswith('20') and len(s) == 7:
            return self.card(s)
        if s.startswith('50') and len(s) == 7:
            return '仪式「' + self.g.name_of('rite', s) + '」'
        if s.startswith('5') and len(s) == 7:
            return '事件「' + self.g.name_of('event', s) + '」'
        return s

    def counter(self, cid):
        n = self.cn.get(str(cid))
        return f'计数器{cid}' + (f'「{n}」' if n else '')

    # ---------- 条件 ----------
    def cond(self, c, depth=0):
        if not c:
            return '无条件'
        if not isinstance(c, dict):
            return str(c)
        parts = []
        for k, v in c.items():
            parts.append(self.cond_key(base_key(k), v, depth))
        return '；'.join(p for p in parts if p)

    def cond_key(self, k, v, depth):
        if k in ('any', 'all'):
            inner = [self.cond_key(base_key(kk), vv, depth + 1) for kk, vv in (v or {}).items()] if isinstance(v, dict) else [str(v)]
            return ('任一[' if k == 'any' else '全部[') + ' | '.join(inner) + ']'
        neg = k.startswith('!')
        kk = k[1:] if neg else k
        op = next((o for o in OPS if kk.endswith(o)), '')
        if op:
            kk = kk[: -len(op)]
        opw = {'>=': '≥', '<=': '≤', '=': '=', '>': '>', '<': '<', '': ''}[op]
        # 检定 r1:表达式
        m = re.match(r'r\d+:(.+)', kk)
        if m:
            need = v[0] if isinstance(v, list) else v
            return f'检定（{m[1]}）{opw or "≥"}{need} 个成功'
        if kk.startswith('f:'):
            return f'公式 {kk[2:]}{opw}{v}'
        m = re.match(r'(s\d+)(?:\.(.+))?$', kk)
        if m:
            slot, rest = m[1], m[2]
            if not rest:
                return f'{slot}{"空着" if neg else "有卡"}'
            return ('非' if neg else '') + f'{slot}' + self.attr(rest, opw, v)
        m = re.match(r'(counter|global_counter)\.(\d+)$', kk)
        if m:
            return ('非' if neg else '') + f'{self.counter(m[2])}{opw or "≥"}{v}'
        m = re.match(r'(have|hand_have|table_have)\.(.+)$', kk)
        if m:
            where = {'have': '拥有', 'hand_have': '手牌区有', 'table_have': '场上有'}[m[1]]
            parts = m[2].split('.')
            obj = self.thing(parts[0]) if parts[0].isdigit() else parts[0]
            extra = f'（带{".".join(parts[1:])}）' if len(parts) > 1 else ''
            return ('没有' if neg else where) + obj + extra + (f'{opw}{v}' if op else '')
        if kk == 'rite':
            return ('没有' if neg else '有') + self.thing(v)
        return ('非' if neg else '') + self.attr(kk, opw, v)

    def attr(self, a, opw, v):
        if a == 'is':
            return f'是「{self.card(v)}」'
        if a == 'type':
            return f'类型={TYPE.get(v, v)}'
        if a == 'rare':
            return f'品级{opw or "="}{RARE.get(v, v)}'
        if a.startswith('cost.'):
            return f'交出{a[5:]}{opw or "≥"}{v}'
        return f'{a}{opw or "≥"}{v}'

    # ---------- 动作 ----------
    def acts(self, a):
        if not a:
            return ''
        if not isinstance(a, dict):
            return str(a)
        out = []
        for k, v in a.items():
            s = self.act(base_key(k), v)
            if s:
                out.append(s)
        return '；'.join(out)

    def act(self, k, v):
        if k in ('prompt', 'no_prompt') and isinstance(v, dict):
            txt = (v.get('text') or '').replace('\n', ' ')
            inner = self.acts({kk: vv for kk, vv in v.items() if kk not in ('id', 'text', 'icon')})
            return '；'.join(x for x in ((f'旁白：{txt[:70]}…' if txt else ''), inner) if x)
        if k == 'option' and isinstance(v, dict):
            items = ' / '.join(f'{it.get("tag")}「{it.get("text")}」' for it in v.get('items', []))
            return f'弹出选项：{(v.get("text") or "").replace(chr(10), " ")[:60]}… 选项：{items}'
        if k.startswith('case:'):
            return f'【选{k[5:]}】' + self.acts(v)
        if k in ('success', 'failed'):
            return ('成功时：' if k == 'success' else '失败时：') + self.acts(v)
        if k == 'delay' and isinstance(v, dict):
            return f'{v.get("round")}天后：' + self.acts({kk: vv for kk, vv in v.items() if kk not in ('id', 'round')})
        if k in ('choose',) or k.startswith('pop.') or k.startswith('think_pop') or k.startswith('hand_pop'):
            return ''
        if k == 'over':
            o = self.over.get(str(v)) or {}
            return f'☠进入结局「{o.get("name", v)}」{o.get("sub_name", "")}'
        if k == 'card':
            ids = v if isinstance(v, list) else [v]
            cid = ids[0]
            return f'获得「{self.card(cid)}」' + ('（' + ','.join(map(str, ids[1:])) + '）' if len(ids) > 1 else '')
        if k == 'coin':
            return f'金币{v:+}' if isinstance(v, int) else f'金币{v}'
        if k in ('event_on', 'event_off', 'rite', 'loot', 'delay_off'):
            ids = v if isinstance(v, list) else [v]
            word = {'event_on': '开启', 'event_off': '关闭', 'rite': '开启', 'loot': '抽取', 'delay_off': '取消延迟'}[k]
            names = []
            for i in ids:
                if k == 'loot':
                    lt, _ = self.g.get('loot', i)
                    names.append(f'抽取表「{(lt or {}).get("name", i)}」')
                else:
                    names.append(self.thing(i))
            return word + '、'.join(names)
        m = re.match(r'clean\.(.+)$', k)
        if m:
            t = m[1]
            if re.fullmatch(r's\d+', t):
                return f'☠移除{t}的卡' + (f'×{v}' if isinstance(v, int) and v > 1 else '')
            if t == 'rite':
                return '关闭仪式' + (f'{self.thing(v)}' if v != 1 else '')
            return f'移除「{self.thing(t)}」'
        m = re.match(r'table\.clean\.(.+)$', k)
        if m:
            return f'☠移除场上「{self.thing(m[1])}」'
        m = re.match(r'(global_counter|counter)([+\-=])(\d+)$', k)
        if m:
            if m[1] == 'global_counter':
                return ''
            return f'{self.counter(m[3])}{m[2]}{v}'
        m = re.match(r'(table\.)?(s\d+|\d{7})(\+|-|=|~)(.+)$', k)
        if m:
            who = m[2] if m[2].startswith('s') else self.thing(m[2])
            what = m[4]
            if what == 'equip':
                return f'{who}{"装备" if m[3] == "+" else "卸下"}「{self.card(v)}」'
            return f'{who} {what}{m[3]}{v}'
        m = re.match(r'(s\d+)\.uprare', k)
        if m:
            return f'{m[1]}品级提升'
        if k in ('steam_achievement', 'focus', 'no_show') or k.startswith('focus.'):
            return ''
        return f'{k}:{json.dumps(v, ensure_ascii=False)[:60]}'


SETTLE = (('settlement_prior', '优先结算'), ('settlement', '结算'), ('settlement_extre', '额外结算'),
          ('waiting_round_end_action', '超时'))


def _involved(h, obj):
    """配置里引用到的卡（含头像 cards/2000195 这种），列名字方便按人名检索。"""
    ids = dict.fromkeys(re.findall(r'(?<!\d)(20\d{5})(?!\d)', json.dumps(obj, ensure_ascii=False)))
    names = dict.fromkeys(h.card(i) for i in ids)
    names = [n for n in names if not n.isdigit()]
    return '涉及：' + '、'.join(names[:40]) if names else ''


def _flags(text):
    f = []
    if '2000083' in text or '妻子的不满' in text:
        f.append('⛔妻子的不满')
    if '☠' in text:
        f.append('☠有移除/结局')
    return f


def build_docs(game):
    h = Humanizer(game)
    docs = []
    refs = defaultdict(lambda: {'slot': set(), 'gain': set(), 'mention': set()})  # 卡 id -> 引用

    # ---- 仪式 ----
    for rid, entries in game.dir_index('rite').items():
        v = game.parse(entries[-1][1])
        if not isinstance(v, dict) or 'name' not in v:
            continue
        name = v.get('name')
        lines = [f'仪式「{name}」（id {rid}，地点 {v.get("location")}，持续 {v.get("round_number")} 天，'
                 f'未开始最多等 {v.get("waiting_round") or "不限"} 天）']
        if v.get('text'):
            lines.append('描述：' + v['text'].replace('\n', ' ')[:200])
        if v.get('tips_text'):
            lines.append('提示：' + ' / '.join(t.replace('\n', ' ') for t in v['tips_text']))
        for sk, sv in (v.get('cards_slot') or {}).items():
            cond = sv.get('condition')
            lines.append(f'格{sk}「{(sv.get("text") or "").replace(chr(10), " ")[:80]}」需要：{h.cond(cond)}'
                         + ('（必填）' if not sv.get('is_empty') else '（可空）') + ('（系统自动吸附）' if sv.get('open_adsorb') else ''))
            for cid in re.findall(r'20\d{5}', json.dumps(cond)):
                refs[cid]['slot'].add(f'{name}({sk})')
        for sec, label in SETTLE:
            for i, st in enumerate(v.get(sec) or []):
                if not isinstance(st, dict):
                    continue
                res = h.acts({**(st.get('result') or {}), **(st.get('action') or {})})
                title = st.get('result_title') or ''
                rt = (st.get('result_text') or '').replace('\n', ' ')[:60]
                lines.append(f'{label}{i}：当 {h.cond(st.get("condition"))} → {res or "无效果"}'
                             + (f'〔{title}〕' if title else '') + (f' 「{rt}…」' if rt and not title else ''))
                blob = json.dumps(st, ensure_ascii=False)
                for cid in re.findall(r'"card(?:#\d+)?":\s*\[?(20\d{5})', blob):
                    refs[cid]['gain'].add(name)
        inv = _involved(h, v)
        if inv:
            lines.append(inv)
        text = '\n'.join(lines)
        if '2000083' in json.dumps(v, ensure_ascii=False):
            text += '\n⛔ 有分支会给「妻子的不满」'
        docs.append({'id': f'rite:{rid}', 'kind': 'rite', 'name': name, 'text': text, 'flags': _flags(text)})

    # ---- 事件 ----
    for eid, entries in game.dir_index('event').items():
        v = game.parse(entries[-1][1])
        if not isinstance(v, dict):
            continue
        name = (v.get('text') or str(eid)).replace('\n', ' ')[:40]
        lines = [f'事件「{name}」（id {eid}，触发时机 {json.dumps(v.get("on"), ensure_ascii=False)}，'
                 f'{"可重复" if v.get("is_replay") else "一次性"}）',
                 '条件：' + h.cond(v.get('condition'))]
        for i, st in enumerate(v.get('settlement') or []):
            if isinstance(st, dict):
                lines.append(f'效果{i}：' + (h.acts(st.get('action')) or '无'))
                for cid in re.findall(r'"card(?:#\d+)?":\s*\[?(20\d{5})', json.dumps(st, ensure_ascii=False)):
                    refs[cid]['gain'].add(f'事件{name}')
        inv = _involved(h, v)
        if inv:
            lines.append(inv)
        text = '\n'.join(lines)
        docs.append({'id': f'event:{eid}', 'kind': 'event', 'name': name, 'text': text, 'flags': _flags(text)})

    # ---- 卡牌 ----
    cards, _ = game.merged('card')
    for cid, c in cards.items():
        if not isinstance(c, dict):
            continue
        name = (c.get('name') or cid).splitlines()[0]
        tags = c.get('tag') or {}
        lines = [f'卡牌「{name}」（id {cid}，{TYPE.get(c.get("type"), c.get("type"))}，品级 {RARE.get(c.get("rare"), c.get("rare"))}）'
                 + (f' 称号：{c["title"]}' if c.get('title') else '')]
        if c.get('text'):
            lines.append('描述：' + c['text'].replace('\n', ' ')[:160])
        if tags:
            lines.append('标签：' + '、'.join(f'{k}{v}' if v != 1 else k for k, v in tags.items()))
        if tags.get('装备'):
            lines.append('装备后给穿戴者：' + '、'.join(f'{k}{v:+}' for k, v in tags.items()
                                                 if isinstance(v, int) and k not in ('装备', '已装备', '饰品', '武器', '服装', '坐骑', '纯色材质', '已拥有')))
        if c.get('card_vanishing'):
            lines.append(f'存在 {c["card_vanishing"]} 天后消失')
        r = refs.get(cid)
        if r:
            if r['slot']:
                lines.append('能放入：' + '、'.join(sorted(r['slot'])[:40]))
            if r['gain']:
                lines.append('获得途径：' + '、'.join(sorted(r['gain'])[:30]))
        text = '\n'.join(lines)
        docs.append({'id': f'card:{cid}', 'kind': 'card', 'name': name, 'text': text, 'flags': []})

    # ---- 结局 ----
    for oid, o in h.over.items():
        if isinstance(o, dict) and o.get('name'):
            text = f'结局「{o["name"]}」（over {oid}）{o.get("sub_name", "")}\n{(o.get("text") or "").replace(chr(10), " ")[:200]}'
            docs.append({'id': f'over:{oid}', 'kind': 'over', 'name': o['name'], 'text': text, 'flags': []})

    # ---- 人工经验 ----
    docs.extend(seed_docs())
    return docs


def seed_docs():
    out = []
    for p, stem in local_state.seed_files():
        chunks = re.split(r'\n(?=## )', p.read_text(encoding='utf-8'))
        for i, ch in enumerate(chunks):
            ch = ch.strip()
            if not ch or not ch.startswith('## '):
                continue
            title = ch.splitlines()[0][3:].strip()
            out.append({'id': f'seed:{stem}#{i}', 'kind': 'seed', 'name': title, 'text': ch, 'flags': _flags(ch)})
    return out


# ---------------- BM25 ----------------

def tokens(s):
    s = s.lower()
    out = re.findall(r'[a-z]+|\d+', s)
    cjk = re.findall(r'[一-鿿]+', s)
    for run in cjk:
        out.extend(run)                                   # 单字
        out.extend(run[i:i + 2] for i in range(len(run) - 1))  # 二字
    return out


class Index:
    def __init__(self, docs):
        self.docs = docs
        self.tf = []
        df = Counter()
        lens = []
        for d in docs:
            # 名字重复 3 次加权，种子经验再加权
            body = (d['name'] + '\n') * 3 + d['text']
            t = Counter(tokens(body))
            self.tf.append(t)
            lens.append(sum(t.values()))
            df.update(t.keys())
        self.n = len(docs)
        self.avg = sum(lens) / max(1, self.n)
        self.lens = lens
        self.idf = {w: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query, k=8, kinds=None, k1=1.4, b=0.75):
        q = Counter(tokens(query))
        scores = []
        for i, d in enumerate(self.docs):
            if kinds and d['kind'] not in kinds:
                continue
            tf = self.tf[i]
            sc = 0.0
            for w, qn in q.items():
                f = tf.get(w)
                if not f:
                    continue
                sc += self.idf.get(w, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lens[i] / self.avg))
            if sc:
                if d['kind'] == 'seed':
                    sc *= 1.6
                if d['name'] and d['name'] in query:
                    sc *= 1.5
                scores.append((sc, i))
        scores.sort(reverse=True)
        return [(s, self.docs[i]) for s, i in scores[:k]]


def snippet(doc, query, width=3):
    q = set(tokens(query))
    lines = doc['text'].splitlines()
    ranked = sorted(range(len(lines)), key=lambda i: -len(q & set(tokens(lines[i]))))
    keep = sorted(set([0] + ranked[:width]))
    return '\n'.join(lines[i][:220] for i in keep)


def _signature(game):
    sig = []
    for src, d in game.layers():
        if src == 'base' and not (d / 'cards.json').is_file():
            raise FileNotFoundError("缺少游戏配置 cards.json，请检查 SULTAN_GAME_DIR")
        sig.append((src, str(d.resolve())))
        for sub in ('rite', 'event', 'loot'):
            p = d / sub
            if p.is_dir():
                sig.append((src, sub, p.stat().st_mtime))
        for f in ('cards.json', 'over.json', 'tag.json'):
            p = d / f
            if p.exists():
                sig.append((src, f, p.stat().st_mtime))
    sig.extend(('seed', stem, p.stat().st_mtime_ns) for p, stem in local_state.seed_files())
    return (KB_VERSION, tuple(sig))


_INDEX = {'sig': None, 'idx': None}


def get_index(game, force=False):
    sig = _signature(game)
    if not force and _INDEX['sig'] == sig:
        return _INDEX['idx']
    docs = None if force else local_state.read_docs(sig)
    if docs is None:
        docs = build_docs(game)
        local_state.write_docs(sig, docs)
    idx = Index(docs)
    _INDEX.update(sig=sig, idx=idx)
    return idx
