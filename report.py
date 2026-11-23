"""每日（游戏内的一天）日报：生成 Markdown，存到 reports\\day_NNN.md。

日报是数据；建议由 Claude 读完日报后写进 day_NNN.advice.md，渲染时附在末尾。
"""

import datetime
import json
import re
from collections import defaultdict
from pathlib import Path

from conditions import check_card, walk_actions
from gamedata import SPECIAL_COUNTERS
from jsonc import base_key

from local_state import STATE as ROOT
REPORTS = ROOT / 'reports'
GOALS = ROOT / 'goals.json'
JOURNAL = REPORTS / 'journal.md'
SETTLE_SECTIONS = ('settlement_prior', 'settlement', 'settlement_extre', 'waiting_round_end_action')


def load_goals():
    try:
        return json.loads(GOALS.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def report_path(day):
    return REPORTS / f'day_{int(day):03d}.md'


def advice_path(day):
    return REPORTS / f'day_{int(day):03d}.advice.md'


def _short(obj, n=160):
    s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    s = s.replace('\n', ' ')
    return s if len(s) <= n else s[:n] + '…'


def _card_args(game, c):
    cfg = game.card_cfg(c['id'])
    return (cfg.get('type'), c['id'], cfg.get('rare', 0) + (c.get('rareup') or 0), game.effective_tags(c),
            c.get('count', 1))


def slot_candidates(game, save, rite):
    """未开始仪式的每个格子：手上（不在别的仪式里）我方能放的卡。"""
    cfg, _ = game.get('rite', rite['id'])
    hand = [c for c, where in game.all_cards(save) if not where and game.owned(c)]
    out = {}
    for sk, sv in (cfg or {}).get('cards_slot', {}).items():
        yes, maybe = [], []
        for c in hand:
            r = check_card(sv.get('condition'), *_card_args(game, c))
            label = f"{game.card_cfg(c['id']).get('name', c['id'])}#{c['uid']}"
            if r is True:
                yes.append(label)
            elif r is None:
                maybe.append(label)
        out[sk] = {'text': (sv.get('text') or '').strip(), 'required': not sv.get('is_empty'), 'auto': bool(sv.get('open_adsorb')),
                   'fits': yes, 'depends': maybe}
    return out


def cross_check(game, save, rite):
    """把仪式的格子规则和我方人物逐个对照：

    - 每格：是否必填（is_empty=0）、是否系统自动吸附（open_adsorb）、已放了什么
    - 每格能放的我方卡：空闲的 / 正在别处忙的（列出在哪）
    - 结算和提示里点名的人：按卡牌 id 核对你有没有、是不是同一个 id 版本、现在在哪
    """
    cfg, _ = game.get('rite', rite['id'])
    cfg = cfg or {}
    slots = list((cfg.get('cards_slot') or {}).items())
    placed = {slots[i][0]: c for i, c in enumerate(rite.get('cards') or []) if c and i < len(slots)}
    mine = [(c, where) for c, where in game.all_cards(save) if game.owned(c)]
    out = {'rite': cfg.get('name'), 'uid': rite['uid'], 'started': rite.get('start'), 'slots': {}, 'named': []}
    for sk, sv in slots:
        free, busy = [], []
        for c, where in mine:
            if (where or '').startswith(f'仪式「{cfg.get("name")}」(uid {rite["uid"]})'):
                continue
            if check_card(sv.get('condition'), *_card_args(game, c)) is not False:
                label = f"{game.card_cfg(c['id']).get('name', c['id'])}#{c['uid']}"
                (busy if where else free).append(label + (f'（{re.sub(r"[(]uid [0-9]+[)] ", "", where)}）' if where else ''))
        p = placed.get(sk)
        out['slots'][sk] = {
            'text': (sv.get('text') or '').split('\n')[0][:40],
            'required': not sv.get('is_empty'), 'auto_adsorb': bool(sv.get('open_adsorb')),
            'placed': f"{game.card_cfg(p['id']).get('name')}#{p['uid']}" if p else None,
            'free': free, 'busy_elsewhere': busy,
        }
    # 结算/提示点名的卡（人物优先）
    blob = json.dumps({k: cfg.get(k) for k in ('settlement_prior', 'settlement', 'settlement_extre', 'tips_text', 'cards_slot')},
                      ensure_ascii=False)
    ids = dict.fromkeys(re.findall(r'(?<!\d)(20\d{5})(?!\d)', blob))
    by_id = defaultdict(list)
    by_name = defaultdict(list)
    for c, where in game.all_cards(save):
        by_id[str(c['id'])].append((c, where))
        by_name[game.card_cfg(c['id']).get('name')].append((c, where))
    for cid in ids:
        ccfg = game.card_cfg(cid)
        if ccfg.get('type') != 'char':
            continue
        name = ccfg.get('name')
        exact = [(c, w) for c, w in by_id.get(cid, []) if game.owned(c)]
        same_name = [(c, w) for c, w in by_name.get(name, []) if str(c['id']) != cid and game.owned(c)]
        row = {'name': name, 'id': cid}
        if exact:
            c, w = exact[0]
            row['status'] = '✅ 有，id 对得上'
            row['where'] = re.sub(r'[(]uid [0-9]+[)] ', '', w) if w else '手牌区（空闲）'
            row['uid'] = c['uid']
        elif same_name:
            c, w = same_name[0]
            row['status'] = f'⚠️ 有同名卡但 id 是 {c["id"]}，这一支不会触发'
            row['where'] = w or '手牌区'
        else:
            row['status'] = '❌ 你没有这个人'
        out['named'].append(row)
    out['notes'] = ('required=必填（红色卡槽，空着不能启动）；auto_adsorb=系统会自动把符合的卡吸进来；'
                    'free=空闲可放，busy_elsewhere=符合条件但正在别的仪式/装备中；named 按卡牌 id 核对点名的人物。')
    return out


# 终局链条（改朝换代 → 于黎明前 → 决战 → 果实），由剧情链自动追出来的 20 个仪式；群龙无首是合成正当性的地方
ENDGAME_RITES = ['5001023', '5010007', '5010008', '5010009', '5010010', '5010011', '5010012', '5010013', '5010014',
                 '5010015', '5010016', '5010017', '5010018', '5010019', '5010021', '5010022', '5010023', '5010024',
                 '5010025', '5010033']
SYNTH_RITES = ['5001004']
GENERIC_TAGS = {'消耗品', '金币', '可堆叠', '已拥有', '纯色材质', '装备', '已装备'}


def long_term_assets(game, save):
    """长线资产：我方物品/苏丹卡里，能**专门**放进终局仪式或群龙无首某一格的（只收通用消耗品/金币的格子不算）。"""
    rites = [(rid, game.get('rite', rid)[0]) for rid in ENDGAME_RITES + SYNTH_RITES]
    out = {}
    for c, where in game.all_cards(save):
        cfg = game.card_cfg(c['id'])
        if cfg.get('type') not in ('item', 'sudan') or not game.owned(c):
            continue
        own = {k for k, v in game.own_tags(c).items() if v and k not in GENERIC_TAGS}
        uses = []
        for rid, rcfg in rites:
            for sk, sv in (rcfg or {}).get('cards_slot', {}).items():
                cond = sv.get('condition')
                blob = json.dumps(cond, ensure_ascii=False)
                if str(c['id']) not in blob and not any(re.search(rf'(?<![一-鿿]){re.escape(t)}', blob) for t in own):
                    continue
                if check_card(cond, *_card_args(game, c)) is not False:
                    uses.append(f"{rcfg.get('name')}({sk})")
        if uses:
            name = (cfg.get('name') or str(c['id'])).split('\n')[0]
            e = out.setdefault(name, {'count': 0, 'uses': set(), 'where': set()})
            e['count'] += c.get('count', 1)
            e['uses'].update(uses)
            if where:
                e['where'].add(re.sub(r'[(]uid [0-9]+[)] ', '', where))
    return {k: {'count': v['count'], 'uses': sorted(v['uses']), 'where': sorted(v['where'])} for k, v in out.items()}


def scarce_resources(game, save):
    """一次性/稀缺资源：用掉就没了，长线要算着用。"""
    c = save.get('counter', {})
    rites = {game.name_of('rite', r['id']) for r in save.get('rites', [])}
    try:
        back = game.global_save().get('backToPrevRound')
    except Exception:
        back = None
    return {
        '金骰子': c.get('7100006', 0),
        '回到前一天（全局存档 backToPrevRound）': back,
        '净化之火（销毁任意苏丹卡，限 1 次）': '在场' if '净化之火' in rites else '不在场/已用',
        '复原的神迹（重置苏丹卡时间，限 3 次）': (f"已用 {c.get('7000163', 0)}/3" if '复原的神迹' in rites
                                         else '不在场/已用完'),
        '苏丹卡池剩余': len(save.get('sudan_card_pool', [])),
    }


def _kind_of(game, i):
    for kind in ('rite', 'event'):
        if game.dir_index(kind).get(str(i)):
            return kind
    return None


def _links(obj, rites, events, counters):
    """递归收集解析后配置里开出的仪式（"rite"）、事件（"event_on"）和读写的计数器。注释已被解析器丢掉。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            b = base_key(k)
            ids = v if isinstance(v, list) else [v]
            if b == 'rite':
                rites += [int(x) for x in ids if str(x).isdigit()]
            elif b == 'event_on':
                events += [int(x) for x in ids if str(x).isdigit()]
            m = re.match(r'counter[.+=-]?(\d{7})', b)
            if m:
                counters.add(m.group(1))
            _links(v, rites, events, counters)
    elif isinstance(obj, list):
        for v in obj:
            _links(v, rites, events, counters)


def line_status(game, save, roots, depth=6):
    """任务线进度：从起点仪式/事件沿配置往下走，按存档 end_rites/only_rites/event_status 标每个节点的真实状态。"""
    end = {int(k): v for k, v in save.get('end_rites', {}).items()}
    seen_rites = set(save.get('only_rites', []))
    evs = {int(k): v for k, v in save.get('event_status', {}).items()}
    cnt = save.get('counter', {})
    on_table = {r['id'] for r in save.get('rites', [])}
    lines, visited = [], set()

    def state(kind, i):
        if kind == 'rite':
            if i in on_table:
                return '🟡在场'
            if end.get(i):
                return f'✅完成×{end[i]}'
            return '⚪出现过（超时/关闭）' if i in seen_rites else '·未出现'
        if i not in evs:
            return '·未开启'
        return '🟡已开启待触发' if evs[i] else '✅已触发/已关'

    def go(i, d):
        kind = _kind_of(game, i)
        pre = '  ' * d
        if i in visited:
            lines.append(f'{pre}{i} {game.name_of(kind or "rite", i)} ↺')
            return
        visited.add(i)
        if not kind:
            lines.append(f'{pre}{i} ?（配置里没有）')
            return
        cfg, _ = game.get(kind, i)
        rites, events, counters = [], [], set()
        _links(cfg, rites, events, counters)
        cs = ' '.join(f'{c}={cnt.get(c, 0)}' for c in sorted(counters)[:6])
        lines.append(f'{pre}{i} [{kind}] {game.name_of(kind, i)} {state(kind, i)}' + (f'  计数 {cs}' if cs else ''))
        if d < depth:
            for x in dict.fromkeys(rites + events):
                if x != i:
                    go(x, d + 1)

    for r in roots:
        go(int(r), 0)
    return lines


def danger_branches(game, rite_id, slot):
    """结算里会移除该格子的卡（clean.sN）、给它加死亡类标签或直接进结局的分支。"""
    cfg, _ = game.get('rite', rite_id)
    pat_clean = f'clean.{slot}'
    pat_tag = re.compile(rf'^{slot}[+=].*(死|亡|尸)')
    hits = []
    for sec in SETTLE_SECTIONS:
        for i, st in enumerate((cfg or {}).get(sec) or []):
            if not isinstance(st, dict):
                continue
            kinds = set()
            for k, v in walk_actions([st.get('action'), st.get('result')]):
                bk = base_key(k)
                if bk == pat_clean:
                    kinds.add('移除该格的卡')
                elif bk == 'over':
                    kinds.add(f'进入结局 {v}')
                elif pat_tag.match(bk):
                    kinds.add(bk)
            if kinds:
                hits.append({'section': sec, 'index': i, 'what': sorted(kinds),
                             'title': st.get('result_title') or '', 'condition': _short(st.get('condition'))})
    return hits


WIFE_UPSET_CARD = 2000083  # 妻子的不满


def upset_wife_branches(game, rite_id):
    """结算里会给「妻子的不满」的分支数。用于提示玩家按本局目标选择。"""
    cfg, _ = game.get('rite', rite_id)
    n = 0
    for sec in SETTLE_SECTIONS:
        for st in (cfg or {}).get(sec) or []:
            if not isinstance(st, dict):
                continue
            for k, v in walk_actions([st.get('action'), st.get('result')]):
                vals = v if isinstance(v, list) else [v]
                if base_key(k) == 'card' and WIFE_UPSET_CARD in vals:
                    n += 1
                    break
    return n


def build(game, diff_fn, save_name='auto'):
    """diff_fn(from_save, to_save) 由 server 传入（就是 diff 工具），避免循环导入。"""
    s = game.load_save(save_name)
    day = s.get('round', 0)
    names = game.counter_names()
    goals = load_goals()
    lines = []
    w = lines.append

    w(f"# 第 {day} 天日报 · {s.get('name')} · 难度 {s.get('difficulty')}")
    w(f"> 存档时间 {s.get('saveTime', '')[:19].replace('T', ' ')}，生成于 {datetime.datetime.now():%Y-%m-%d %H:%M}。"
      f" mod：{'、'.join(m['name'] for m in game.mod_names()) or '无'}")
    w('')
    if goals:
        w('**本局目标**：' + '；'.join(goals.get('objectives', [])) +
          '。**必须保护**：' + '、'.join(goals.get('protect', [])) +
          '。可接受：' + '；'.join(goals.get('acceptable', [])) + '。')
        for rule in goals.get('hard_rules', []):
            w(f'**硬规则**：{rule}')
        w('')

    cards = game.all_cards(s)
    mine = [(c, where) for c, where in cards if game.owned(c)]

    # ---- 一、要紧事 ----
    w('## 一、要紧事')
    urgent = []
    for c, where in mine:
        d = game.describe_card(c, where=where)
        if d['type'] == 'sudan':
            uses = []
            for r in s.get('rites', []):
                if r.get('start'):
                    continue
                rcfg, _ = game.get('rite', r['id'])
                for sk, sv in (rcfg or {}).get('cards_slot', {}).items():
                    if check_card(sv.get('condition'), *_card_args(game, c)) is True:
                        uses.append(f"{game.name_of('rite', r['id'])}({sk})")
            place = f"，已放在{where}" if where else ''
            use = f"当前可放入：{'、'.join(uses)}" if uses else '当前场上没有直接能放它的格子。'
            urgent.append(f"- 苏丹卡 **{d['name']}**（品级 {d['rare']}）剩 **{d.get('days_left')}** 天{place}。{use}")
    for r in s.get('rites', []):
        rcfg, _ = game.get('rite', r['id'])
        wr = (rcfg or {}).get('waiting_round') or 0
        if not r.get('start') and wr > 0:
            left = wr - r.get('life', 0)
            if left <= 1:
                timeout = [t.get('result_title') or t.get('result_text') or t.get('action')
                           for t in (rcfg or {}).get('waiting_round_end_action') or []]
                timeout = [t for t in timeout if t]
                tail = f" 超时后果：{_short(timeout, 120)}" if timeout else ''
                urgent.append(f"- 仪式 **{game.name_of('rite', r['id'])}**（{rcfg.get('location')}）今天不开始就会被移除。{tail}")
    for c, where in mine:
        d = game.describe_card(c, where=where)
        if d['type'] != 'sudan' and d.get('days_left') is not None and d['days_left'] <= 3:
            urgent.append(f"- **{d['name']}** 还剩 {d['days_left']} 天消失")
    w('\n'.join(urgent) if urgent else '- 无')
    w('')

    # ---- 关注人物 ----
    protect = goals.get('protect', [])
    if protect:
        w('## 二、关注人物')
        for name in protect:
            found = [(c, where) for c, where in cards if game.card_cfg(c['id']).get('name') == name]
            if not found:
                w(f'- ⚠️ **{name}** 不在场上（可能已死亡或离开），请立刻确认！')
                continue
            for c, where in found:
                d = game.describe_card(c, where=where)
                attrs = '、'.join(f'{k}{v}' for k, v in d['tags'].items() if k in ('体魄', '魅力', '智慧', '战斗', '社交', '生存', '隐匿', '魔力', '激情'))
                w(f"- **{name}**#{d['uid']}：{'我方' if d['owned'] else '⚠️ 不是追随者'}，{attrs}；"
                  f"位置：{where or '手牌区'}。")
                m = re.search(r'\(uid (\d+)\) 格 (s\d+)', where or '')
                if m:
                    rite = next(r for r in s['rites'] if r['uid'] == int(m[1]))
                    risks = danger_branches(game, rite['id'], m[2])
                    if risks:
                        w(f"  - ⚠️ 这个仪式有 {len(risks)} 个分支会动到 {m[2]}：")
                        for h in risks[:6]:
                            w(f"    - {h['section']}[{h['index']}] {'/'.join(h['what'])} {h['title']} 条件：`{h['condition']}`")
                    else:
                        w(f"  - 结算里没有移除 {m[2]} 或进结局的分支。")
        w('')

    # ---- 昨夜结算 ----
    w('## 三、昨夜结算')
    notes = s.get('notes', [])
    if notes:
        groups = {}
        for n in notes[-1]:
            t = n.get('type')
            if t == 4:
                txt = f"{game.card_cfg(n.get('count')).get('name', n.get('count'))}→{game.thing_name(n.get('id'))}"
            else:
                txt = game.thing_name(n.get('id')) + (f"×{n['count']}" if n.get('count', 0) > 1 else '')
            groups.setdefault(t, []).append(txt)
        labels = {1: '新出现', 2: '超时移除', 3: '完成', 4: '卷入', 10001: '新追随者', 10002: '获得'}
        for t, items in groups.items():
            w(f"- {labels.get(t, t)}：{'、'.join(items)}")
    prev = None
    for cand in (f'{day - 1}', f'{day - 1}_end'):
        try:
            game.save_path(cand)
            prev = cand
            break
        except FileNotFoundError:
            continue
    if prev:
        diff = diff_fn(prev, save_name)
        if diff['characters_joined']:
            w(f"- 新加入：{'、'.join(x['name'] for x in diff['characters_joined'])}")
        if diff['characters_left']:
            w(f"- ⚠️ 离开/失去：{'、'.join(x['name'] for x in diff['characters_left'])}")
        if diff['character_tag_changes']:
            w('- 属性变化：' + '；'.join(
                f"{x['name']} " + ' '.join(f"{k}{v[0] or 0}→{v[1] or 0}" for k, v in x['tags'].items())
                for x in diff['character_tag_changes']))
        if diff['item_changes']:
            w('- 物品：' + '、'.join(f"{x['name']} {x['from']}→{x['to']}" for x in diff['item_changes']))
        res = [x for x in diff['counter_changes'] if x['id'] in SPECIAL_COUNTERS]
        if res:
            w('- 资源：' + '、'.join(f"{x['name']} {x['from']}→{x['to']}" for x in res))
        w(f"- （对比 {diff['from']} → {diff['to']}）")
    w('')

    # ---- 资源 ----
    counter = s.get('counter', {})
    w('## 四、资源')
    w(' · '.join(f"{names.get(k, k)} {counter.get(k, 0)}" for k in SPECIAL_COUNTERS) +
      f" · 金币 {sum(c.get('count', 1) for c, _ in mine if game.card_cfg(c['id']).get('name') == '金币')}")
    w(f"苏丹卡池剩 {len(s.get('sudan_card_pool', []))} 张，本周期已重抽 {s.get('sudan_redraw_times')} 次。")
    w('')

    # ---- 进行中 ----
    w('## 五、进行中的仪式')
    for r in s.get('rites', []):
        if not r.get('start'):
            continue
        d = game.describe_rite(r)
        rn = d.get('round_number') or 0
        prog = day - (r.get('start_round') or day)
        placed = '、'.join(f"{p['slot']}:{p['name']}" for p in d.get('placed', []))
        w(f"- **{d['name']}**（{d['location']}）第 {prog + 1}/{rn} 天 · {placed or '空'}")
    w('')

    # ---- 待处理 ----
    w('## 六、待处理的仪式')
    pending = []
    for r in s.get('rites', []):
        if r.get('start'):
            continue
        rcfg, _ = game.get('rite', r['id'])
        wr = (rcfg or {}).get('waiting_round') or 0
        left = wr - r.get('life', 0) if wr else None
        pending.append((left if left is not None else 999, r, rcfg))
    pending.sort(key=lambda x: x[0])
    for left, r, rcfg in pending:
        d = game.describe_rite(r)
        due = f"剩 {left} 天" if left != 999 else '不限时'
        w(f"### {d['name']}（{d['location']}，{due}，uid {r['uid']}）")
        upset = upset_wife_branches(game, r['id'])
        if upset:
            w(f"> ⛔ 有 {upset} 个结算分支会给「妻子的不满」。若本局要规避，请先核对所选格子与分支。")
        tips = d.get('tips_text') or []
        if tips:
            w('提示：' + ' / '.join(t.replace('\n', ' ') for t in tips[:4]) + (' …' if len(tips) > 4 else ''))
        if d.get('placed'):
            w('已放：' + '、'.join(f"{p['slot']}:{p['name']}" for p in d['placed']))
        for sk, info in slot_candidates(game, s, r).items():
            fits = info['fits']
            dep = info['depends']
            txt = info['text'].split('\n')[0][:40]
            row = f"- {sk}{'（必填）' if info['required'] else ''}{'（自动吸附）' if info['auto'] else ''} {txt}："
            if fits:
                row += '、'.join(fits[:8]) + (f' 等 {len(fits)} 张' if len(fits) > 8 else '')
            elif dep:
                row += '要看其他格子：' + '、'.join(dep[:5]) + (f' 等 {len(dep)} 张' if len(dep) > 5 else '')
            else:
                row += '我方没有符合的卡'
            w(row)
        w('')

    # ---- 阵容 ----
    w('## 七、我方人物')
    attrs = ['体魄', '魅力', '智慧', '战斗', '社交', '生存', '隐匿', '魔力', '激情', '支持']
    w('| 人物 | 品级 | ' + ' | '.join(attrs) + ' | 位置 |')
    w('|' + '---|' * (len(attrs) + 3))
    for c, where in mine:
        d = game.describe_card(c, where=where)
        if d['type'] != 'char':
            continue
        loc = re.sub(r'\(uid \d+\) ', '', where) if where else ''
        w(f"| {d['name']}{'（替身）' if d.get('note') else ''}#{d['uid']} | {d['rare']} | " + ' | '.join(str(d['tags'].get(a, '')) for a in attrs) + f" | {loc} |")
    w('')

    # ---- 物品栏 ----
    w('## 八、长线资产与物品栏')
    w('> 一~七节看眼下，这一节看长线：下面这些东西终局用得上，消耗前先想清楚。')
    w('**稀缺资源**：' + ' · '.join(f'{k} {v}' for k, v in scarce_resources(game, s).items()))
    lt = long_term_assets(game, s)
    if lt:
        w('**终局用得上的藏品**（改朝换代→于黎明前→决战→果实，含群龙无首合成）：')
        for name, e in sorted(lt.items(), key=lambda x: -len(x[1]['uses'])):
            loc = f"（现在在：{'、'.join(e['where'])}）" if e['where'] else ''
            w(f"- **{name}**×{e['count']}：{'、'.join(e['uses'][:6])}{' 等' if len(e['uses']) > 6 else ''}{loc}")
    inv = game.inventory(s)
    gear = [g for g in inv if g['kind'] == '装备']
    idle = [g for g in gear if g['in_hand']]
    worn = [g for g in gear if g['equipped_on']]
    if idle:
        w('**闲置装备**（在手上没人穿）：')
        for g in idle:
            eff = ' '.join(f'{k}{v:+}' for k, v in (g.get('effect') or {}).items())
            w(f"- {g['name']}（品级 {g['rare']}）×{g['in_hand']}：{eff or '无属性加成'}")
    if worn:
        w('**已装备**：' + '；'.join(f"{g['name']}→{'、'.join(g['equipped_on'])}" for g in worn))
    for kind in ('情报', '思潮', '读物', '消耗品', '其他'):
        rows = [g for g in inv if g['kind'] == kind]
        if rows:
            w(f'**{kind}**：' + '、'.join(
                f"{g['name']}" + (f"(品{g['rare']})" if kind in ('情报', '思潮') else '') +
                (f"×{g['in_hand'] + g['in_rites']}" if g['in_hand'] + g['in_rites'] > 1 else '') +
                ('[仪式中]' if g['in_rites'] and not g['in_hand'] else '')
                for g in rows))
    w('')

    # ---- 建议 ----
    w('## 九、Claude 的建议')
    ap = advice_path(day)
    w(ap.read_text(encoding='utf-8').strip() if ap.exists() else '_（待 Claude 读完日报后填写）_')
    w('')
    return day, '\n'.join(lines)


def generate(game, diff_fn, save_name='auto', force=True):
    REPORTS.mkdir(parents=True, exist_ok=True)
    s = game.load_save(save_name)
    p = report_path(s.get('round', 0))
    if p.exists() and not force:
        return s.get('round', 0), p, p.read_text(encoding='utf-8')
    day, md = build(game, diff_fn, save_name)
    p.write_text(md, encoding='utf-8')
    return day, p, md


def save_advice(game, diff_fn, day, text):
    REPORTS.mkdir(parents=True, exist_ok=True)
    advice_path(day).write_text(text.strip() + '\n', encoding='utf-8')
    cur = game.load_save('auto').get('round')
    if int(day) == cur:
        generate(game, diff_fn, 'auto')
    else:
        # 旧日报只替换建议段，保留当时的数据
        p = report_path(day)
        if p.exists():
            md = p.read_text(encoding='utf-8')
            head = md.split('## 九、Claude 的建议')[0]
            p.write_text(head + '## 九、Claude 的建议\n' + text.strip() + '\n', encoding='utf-8')
    return report_path(day)


def journal_add(day, text):
    REPORTS.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open('a', encoding='utf-8') as f:
        f.write(f"- 第 {day} 天（{datetime.datetime.now():%m-%d %H:%M}）：{text.strip()}\n")
    return JOURNAL


def journal_read(last=30):
    if not JOURNAL.exists():
        return []
    return JOURNAL.read_text(encoding='utf-8').splitlines()[-int(last):]
