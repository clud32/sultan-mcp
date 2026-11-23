"""苏丹的游戏 只读 MCP 服务（stdio，纯标准库）。

读存档和叠加 mod 后的配置，供 Claude 陪玩时查局面、查条件。不写任何游戏文件。
"""

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import local_state
import kb  # noqa: E402
import report  # noqa: E402
from gamedata import Game, SPECIAL_COUNTERS, select_path  # noqa: E402

PROTOCOL = '2025-06-18'
MAX_CHARS = 60000
NOTE_TYPES = {1: '仪式出现', 2: '仪式超时被移除', 3: '仪式完成', 4: '卡被卷入仪式',
              10001: '成为追随者', 10002: '获得卡牌'}
MAIN_ATTRS = ['体魄', '魅力', '智慧', '隐匿', '战斗', '社交', '生存', '支持', '魔力', '激情']

game = Game()


# ---------------- 工具实现 ----------------

def t_overview(save='auto'):
    s = game.load_save(save)
    names = game.counter_names()
    counter = s.get('counter', {})
    sudan, chars, items, expiring = [], [], 0, []
    for c, where in game.all_cards(s):
        d = game.describe_card(c, where=where)
        if not d['owned']:
            continue
        if d['type'] == 'sudan':
            sudan.append({k: d[k] for k in ('uid', 'name', 'rare', 'days_left') if k in d})
        elif d['type'] == 'char':
            attrs = {k: d['tags'][k] for k in MAIN_ATTRS if k in d['tags']}
            row = {'uid': d['uid'], 'name': d['name'], 'rare': d['rare'], 'attrs': attrs}
            if where:
                row['where'] = where
            if d.get('equips'):
                row['equips'] = d['equips']
            chars.append(row)
        else:
            items += 1
        if d.get('days_left') is not None and d['type'] != 'sudan' and d['days_left'] <= 3:
            expiring.append({'uid': d['uid'], 'name': d['name'], 'days_left': d['days_left']})
    rnd = s.get('round', 0)
    return {
        'save': str(game.save_path(save).name), 'saveTime': s.get('saveTime'), 'protagonist': s.get('name'),
        'difficulty': s.get('difficulty'), 'round': rnd,
        'mods': [m['name'] for m in game.mod_names()],
        'resources': {names.get(k, k): counter.get(k, 0) for k in SPECIAL_COUNTERS},
        'sudan_cards_in_hand': sudan,
        'sudan_redraw': {'used_this_cycle': s.get('sudan_redraw_times'), 'per_round': s.get('sudan_redraw_times_per_round')},
        'sudan_pool_left': len(s.get('sudan_card_pool', [])),
        'my_characters': chars,
        'my_item_count': items,
        'idle_equipment': [{k: g[k] for k in ('name', 'rare', 'effect', 'in_hand')}
                           for g in game.inventory(s) if g['kind'] == '装备' and g['in_hand']],
        'expiring_soon': sorted(expiring, key=lambda x: x['days_left']),
        'active_rites': [game.describe_rite(r) for r in s.get('rites', [])],
        'delayed_ops': [{'id': op.get('id'), 'in_days': op.get('round'), 'name': game.thing_name(op.get('id'))}
                        for op in s.get('delay_ops', [])],
        'notes': '人物归属按 追随者/主角 标签判断，物品按 已拥有 标签判断；life 是已存在天数；'
                 'days_left = card_vanishing - life。',
    }


def t_cards(owner='mine', type=None, query=None, uid=None, save='auto', limit=200):
    s = game.load_save(save)
    rows = []
    for c, where in game.all_cards(s):
        if uid is not None and c['uid'] != int(uid):
            continue
        d = game.describe_card(c, full=uid is not None, where=where)
        if uid is None:
            if owner == 'mine' and not d['owned']:
                continue
            if owner == 'others' and d['owned']:
                continue
            if type and d['type'] != type:
                continue
            if query and query not in str(d['name']) and query not in json.dumps(d['tags'], ensure_ascii=False):
                continue
        rows.append(d)
    if uid is not None and not rows:
        return {'error': f'当前存档里没有 uid {uid}'}
    return {'count': len(rows), 'cards': rows[:int(limit)], 'truncated': len(rows) > int(limit)}


def t_rites(uid=None, query=None, save='auto'):
    s = game.load_save(save)
    out = []
    for r in s.get('rites', []):
        if uid is not None and r['uid'] != int(uid):
            continue
        d = game.describe_rite(r, full=uid is not None)
        if query and query not in str(d['name']) and query not in str(d.get('location')):
            continue
        out.append(d)
    return {'round': s.get('round'), 'count': len(out), 'rites': out}


def t_config(kind, id, path=None, raw=False):
    kind = game.norm_kind(kind)
    if raw:
        text, p = game.raw_text(kind, id)
        if text is None:
            return {'error': f'{kind} {id} 不存在'}
        return {'file': p, 'raw': text}
    v, chain = game.get(kind, id)
    if v is None:
        return {'error': f'{kind} {id} 不存在'}
    return {'kind': kind, 'id': str(id), 'name': v.get('name') if isinstance(v, dict) else None,
            'sources': chain, 'path': path or '', 'data': select_path(v, path),
            'notes': '键名后的 #2、#3 表示原文里的重复键（第 2、3 个同名键），各分支都保留了。'}


def t_search(query, kinds=None, limit=30):
    kinds = kinds or ['card', 'rite', 'event', 'tag', 'over']
    return {'hits': game.search(str(query), kinds, int(limit))}


def t_counters(query=None, save='auto', include_zero=False):
    s = game.load_save(save)
    names = game.counter_names()
    out = []
    for k, v in s.get('counter', {}).items():
        if not include_zero and not v:
            continue
        n = names.get(k)
        if query and query not in k and (not n or query not in n):
            continue
        out.append({'id': k, 'name': n, 'value': v})
    return {'count': len(out), 'counters': out,
            'notes': '名字取自配置里引用该计数器时的行尾注释（出现最多的那个），可能不准，没有注释的为 null。'}


def t_saves():
    s = game.load_save('auto')
    return {'dir': str(game.save_dir()), 'current_round': s.get('round'), 'files': game.list_saves()[:40]}


def _owned_map(s):
    m = {}
    for c, where in game.all_cards(s):
        d = game.describe_card(c, where=where)
        if d['owned']:
            m[c['uid']] = d
    return m


def _owned_counts(m):
    """物品按卡牌 id 汇总数量：金币这类堆叠物每天换 uid，按 uid 比会满屏得失。"""
    out = {}
    for d in m.values():
        if d['type'] != 'char':
            out[d['id']] = out.get(d['id'], 0) + d.get('count', 1)
    return out


def t_diff(from_save, to_save='auto'):
    a, b = game.load_save(from_save), game.load_save(to_save)
    ma, mb = _owned_map(a), _owned_map(b)
    joined = [_brief(mb[u]) for u in mb if u not in ma and mb[u]['type'] == 'char']
    left = [_brief(ma[u]) for u in ma if u not in mb and ma[u]['type'] == 'char']
    qa, qb = _owned_counts(ma), _owned_counts(mb)
    items = [{'id': i, 'name': game.card_cfg(i).get('name', i), 'from': qa.get(i, 0), 'to': qb.get(i, 0)}
             for i in sorted(qa.keys() | qb.keys()) if qa.get(i, 0) != qb.get(i, 0)]
    changed = []
    for u in ma.keys() & mb.keys():
        if mb[u]['type'] != 'char':
            continue
        ta, tb = ma[u]['tags'], mb[u]['tags']
        delta = {}
        for k in ta.keys() | tb.keys():
            if ta.get(k) != tb.get(k):
                delta[k] = [ta.get(k), tb.get(k)]
        if delta:
            changed.append({'uid': u, 'name': mb[u]['name'], 'tags': delta})
    names = game.counter_names()
    ca, cb = a.get('counter', {}), b.get('counter', {})
    counters = [{'id': k, 'name': names.get(k), 'from': ca.get(k, 0), 'to': cb.get(k, 0)}
                for k in sorted(ca.keys() | cb.keys()) if ca.get(k, 0) != cb.get(k, 0)]
    # 每天自动重开的仪式会换 uid，按仪式 id 比
    ra = {r['id'] for r in a.get('rites', [])}
    rb = {r['id'] for r in b.get('rites', [])}
    return {
        'from': f"{game.save_path(from_save).name} (第 {a.get('round')} 天)",
        'to': f"{game.save_path(to_save).name} (第 {b.get('round')} 天)",
        'characters_joined': joined, 'characters_left': left,
        'item_changes': items, 'character_tag_changes': changed,
        'counter_changes': counters,
        'rites_new': [game.name_of('rite', i) for i in rb - ra],
        'rites_gone': [game.name_of('rite', i) for i in ra - rb],
    }


def _brief(d):
    return {k: d[k] for k in ('uid', 'name', 'type', 'rare') if k in d}


def t_log(round=None, save='auto'):
    s = game.load_save(save)
    notes = s.get('notes', [])
    if round is None:
        idx = len(notes) - 1
    else:
        idx = int(round) - 1 if int(round) >= 1 else len(notes) + int(round)
    if not 0 <= idx < len(notes):
        return {'error': f'记录共 {len(notes)} 天，没有第 {round} 天'}
    out = []
    for n in notes[idx]:
        t = n.get('type')
        row = {'type': NOTE_TYPES.get(t, t), 'id': n.get('id'), 'name': game.thing_name(n.get('id'))}
        if t == 4:
            # 「卡被卷入仪式」这条的 count 字段装的是被卷入的卡牌 id
            row['card'] = game.card_cfg(n.get('count')).get('name', n.get('count'))
        elif n.get('count'):
            row['count'] = n['count']
        out.append(row)
    return {'day': idx + 1, 'entries': out, 'notes': '类型名按 ui.json 里 NOTE_* 的顺序推断。'}


def t_upcoming(within=3, query=None, save='auto'):
    s = game.load_save(save)
    rnd = s.get('round', 0)
    out = []
    for k, v in s.get('timing_rounds', {}).items():
        if isinstance(v, int) and rnd <= v <= rnd + int(within):
            eid = k[:-2] if len(k) == 9 else k
            name = game.thing_name(eid)
            if query and query not in name and query not in eid:
                continue
            out.append({'event': eid, 'round': v, 'in_days': v - rnd, 'name': name})
    out.sort(key=lambda x: x['in_days'])
    return {'round': rnd, 'count': len(out), 'checks': out[:300],
            'delayed_ops': [{'id': op.get('id'), 'in_days': op.get('round'), 'name': game.thing_name(op.get('id'))}
                            for op in s.get('delay_ops', [])],
            'notes': 'checks 是周期事件下一次检查条件的回合（timing_rounds），条件不满足就不会触发；'
                     '要知道会不会触发，用 config kind=event 读它的 condition。delayed_ops 的 in_days 是剩余天数。'}


def t_daily_report(save='auto', regenerate=True):
    day, path, md = report.generate(game, t_diff, save, force=bool(regenerate))
    return {'day': day, 'file': str(path), 'markdown': md}


def t_slots(uid, save='auto'):
    s = game.load_save(save)
    r = next((r for r in s.get('rites', []) if r['uid'] == int(uid)), None)
    if not r:
        return {'error': f'当前存档里没有仪式 uid {uid}'}
    return {'rite': game.name_of('rite', r['id']), 'started': r.get('start'),
            'slots': report.slot_candidates(game, s, r),
            'notes': 'fits = 单看这张卡就满足格子条件；depends = 条件还引用了别的格子/计数器，要结合起来看。'
                     '只列手牌区（不在其他仪式里）的我方卡，名字后是 uid。判定是尽力而为，关键决定前用 config 读原文。'}


def t_cross_check(uid=None, name=None, save='auto'):
    s = game.load_save(save)
    rites = [r for r in s.get('rites', []) if (uid is not None and r['uid'] == int(uid))
             or (name and name in game.name_of('rite', r['id']))]
    if not rites:
        return {'error': f'场上没有这个仪式（uid={uid} name={name}）'}
    return {'results': [report.cross_check(game, s, r) for r in rites]}


def t_long_term(save='auto'):
    s = game.load_save(save)
    return {'scarce': report.scarce_resources(game, s), 'assets': report.long_term_assets(game, s),
            'endgame_chain': '改朝换代→于黎明前→集结部队/誓师→苏丹的城墙/燃烧的城市/皇宫→直面苏丹(光/剑/王之舞)→帷幕之后/阶梯→果实；支线 古老的道路；合成 群龙无首',
            'notes': '长线资产 = 能专门放进终局仪式或群龙无首某一格的物品（只收通用消耗品/金币的格子不算）。消耗这些前先想清楚终局还要不要。'}


def t_line_status(ids, depth=6, save='auto'):
    s = game.load_save(save)
    return {'day': s.get('round'), 'tree': report.line_status(game, s, ids, int(depth)),
            'legend': '仪式：🟡在场 / ✅完成×N / ⚪出现过（超时或被关）/ ·未出现；事件：🟡已开启待触发 / ✅已触发或已关 / ·未开启。'
                      '沿配置的 rite / event_on 往下走（不含注释），不代表每一步的条件都满足，关键节点再用 config 核对。'}


def t_save_advice(day, text):
    p = report.save_advice(game, t_diff, day, text)
    return {'saved': str(p), 'advice_file': str(report.advice_path(day))}


def t_journal(text=None, last=30):
    if text:
        day = game.load_save('auto').get('round')
        return {'appended_to': str(report.journal_add(day, text))}
    return {'entries': report.journal_read(last)}


def t_goals(update=None):
    g = report.load_goals()
    if update:
        g.update(update)
        report.GOALS.parent.mkdir(parents=True, exist_ok=True)
        report.GOALS.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding='utf-8')
    return g


def t_kb_search(query, kinds=None, k=8, full=False):
    idx = kb.get_index(game)
    hits = idx.search(str(query), k=int(k), kinds=set(kinds) if kinds else None)
    return {'query': query, 'kb_version': kb.KB_VERSION, 'hits': [
        {'id': d['id'], 'kind': d['kind'], 'name': d['name'], 'score': round(sc, 2), 'flags': d['flags'],
         'text': d['text'] if full else kb.snippet(d, str(query))}
        for sc, d in hits],
        'notes': '先看 seed（人工经验），再看转写文档；关键决定前用 config 读原文核对。kb_get 取整篇。'}


def t_kb_get(id):
    idx = kb.get_index(game)
    d = next((d for d in idx.docs if d['id'] == id), None)
    if not d:
        return {'error': f'没有文档 {id}（格式如 rite:5006585、card:2000680、event:5321063、seed:经验#3）'}
    return d


def t_kb_add_seed(title, text, file='经验'):
    """追加一条人工经验（## 标题 + 正文）到本地状态目录的 kb_seeds/<file>.md，下次检索自动重建索引。"""
    p = local_state.note_path(file)
    with p.open('a', encoding='utf-8') as f:
        f.write(f'\n## {title.strip()}\n{text.strip()}\n')
    _log_kb_event(f'种子 +「{title.strip()}」（{file}.md）')
    return {'appended_to': str(p), 'title': title, 'logged_in': str(local_state.STATE / 'CHANGELOG.md')}


def _log_kb_event(line):
    """知识库更新事件自动登记到 CHANGELOG.md 的「未发布」节，发版时归入新版本。"""
    import datetime
    p = local_state.STATE / 'CHANGELOG.md'
    t = p.read_text(encoding='utf-8') if p.exists() else '# 更新记录\n\n## 未发布\n'
    head, sep, rest = t.partition('## 未发布\n')
    if not sep:
        head, sep, rest = t + '\n', '## 未发布\n', ''
    p.write_text(head + sep + f'- {datetime.date.today():%Y-%m-%d} {line}\n' + rest, encoding='utf-8')


def t_kb_rebuild():
    idx = kb.get_index(game, force=True)
    from collections import Counter
    return {'kb_version': kb.KB_VERSION, 'docs': len(idx.docs), 'by_kind': dict(Counter(d['kind'] for d in idx.docs))}


def _ensure_today_report():
    """进入新的一天后，第一次调用任何工具时顺手生成当天日报。"""
    try:
        day = game.load_save('auto').get('round', 0)
        if not report.report_path(day).exists():
            report.generate(game, t_diff, 'auto')
    except Exception:
        traceback.print_exc(file=sys.stderr)


# ---------------- 工具声明 ----------------

S_SAVE = {'type': 'string', 'description': "存档：auto（默认，优先自动存档，缺失时用最近的回合快照）、74（第74天开始）、74_end（第74天结束）"}

TOOLS = [
    ('overview', t_overview, '当前局面总览：天数、善名恶名权势等资源、手里的苏丹卡和剩余天数、我方人物属性、'
     '即将消失的卡、进行中的仪式（放了谁）、延迟操作。每次出主意前先调它。',
     {'save': S_SAVE}, []),
    ('cards', t_cards, '列卡牌（含放在仪式格子里的）。owner=mine/others/all；type=char/item/sudan；query 按名字或标签过滤；'
     '给 uid 时返回这张卡的完整信息（文本、存档原始 tag、装备槽等）。',
     {'owner': {'type': 'string', 'enum': ['mine', 'others', 'all']},
      'type': {'type': 'string', 'enum': ['char', 'item', 'sudan']},
      'query': {'type': 'string'}, 'uid': {'type': 'integer'}, 'save': S_SAVE,
      'limit': {'type': 'integer'}}, []),
    ('rites', t_rites, '列场上的仪式/事件。给 uid 时附带每个格子的放卡条件、开启条件和配置来源（原版还是哪个 mod）。',
     {'uid': {'type': 'integer'}, 'query': {'type': 'string', 'description': '按名字或地点过滤'}, 'save': S_SAVE}, []),
    ('config', t_config, '读叠加 mod 后生效的配置。kind=card/tag/over/rite/event/loot/...；id 是配置 id（卡用 200xxxx，'
     '仪式 50xxxxx，标签用英文 code）。path 取子树（如 cards_slot.s3、settlement.0.condition）以免输出过长。'
     '重复键保留为 键#2、键#3。raw=true 返回原始文件文本（含注释，仅目录型配置）。判断条件前用它读原文。',
     {'kind': {'type': 'string'}, 'id': {'type': 'string'}, 'path': {'type': 'string'},
      'raw': {'type': 'boolean'}}, ['kind', 'id']),
    ('search', t_search, '在配置里按关键词搜（名字、文本，目录型配置搜全文）。kinds 默认 card/rite/event/tag/over。',
     {'query': {'type': 'string'}, 'kinds': {'type': 'array', 'items': {'type': 'string'}},
      'limit': {'type': 'integer'}}, ['query']),
    ('counters', t_counters, '存档里的计数器（剧情进度、开关等），附从配置注释推断的名字。query 按 id 或名字过滤。',
     {'query': {'type': 'string'}, 'save': S_SAVE, 'include_zero': {'type': 'boolean'}}, []),
    ('saves', t_saves, '列出存档目录里的 auto_save 和每天的 round_N / round_N_end 文件（新的在前）。', {}, []),
    ('diff', t_diff, '比较两个存档：我方得失的卡、属性/标签变化、计数器变化、仪式增减。用来复盘某一天发生了什么。',
     {'from_save': S_SAVE, 'to_save': S_SAVE}, ['from_save']),
    ('log', t_log, '游戏内每日通知记录（仪式出现/完成/超时、获得卡牌、成为追随者）。round 不填为最近一天，负数倒数。',
     {'round': {'type': 'integer'}, 'save': S_SAVE}, []),
    ('upcoming', t_upcoming, '周期事件在 within 天内（默认 3）下一次检查条件的时间，以及延迟操作。'
     '只是检查时间，不代表会触发。query 按事件名或 id 过滤。',
     {'within': {'type': 'integer'}, 'query': {'type': 'string'}, 'save': S_SAVE}, []),
    ('daily_report', t_daily_report, '生成（或重新生成）当天日报，存到 本地 reports/day_NNN.md 并返回全文：'
     '本局目标、要紧事（苏丹卡能放哪、今天不处理就超时的仪式、快消失的卡）、关注人物（所在格子有没有被移除/进结局的分支）、'
     '昨夜结算、资源、进行中/待处理仪式（每格我方候选）、人物属性表、Claude 的建议。'
     '每天开局先读它，逐项处理后用 save_advice 写建议。',
     {'save': S_SAVE, 'regenerate': {'type': 'boolean', 'description': 'false 时已有就直接读，不重算'}}, []),
    ('slots', t_slots, '某个仪式（uid）每个格子我方手上能放哪些卡。',
     {'uid': {'type': 'integer'}, 'save': S_SAVE}, ['uid']),
    ('cross_check', t_cross_check, '人物交叉核对：某个仪式（uid 或名字）每格是否必填、是否自动吸附、已放了什么，'
     '我方哪些卡能放（空闲的 / 正在别处忙的、在哪），以及结算/提示里点名的人物你有没有、id 版本对不对、现在在哪。'
     '不确定"我有没有某人、为什么放不进去"时先用它。',
     {'uid': {'type': 'integer'}, 'name': {'type': 'string'}, 'save': S_SAVE}, []),
    ('long_term', t_long_term, '长线视角：稀缺资源（金骰子、回退次数、净化之火、复原的神迹、苏丹卡池）+ 终局用得上的藏品'
     '（能放进改朝换代→决战→果实各仪式或群龙无首的物品，及对应格子）。给建议时和眼下的日报分开看。',
     {'save': S_SAVE}, []),
    ('line_status', t_line_status, '任务线进度：从起点仪式/事件 id 沿配置往下走，逐节点标存档里的真实状态'
     '（在场/完成几次/出现过/未出现；事件已开启/已触发/未开启）并带出相关计数器。问"这条线走到哪了、还能不能走"时先用它，别猜。',
     {'ids': {'type': 'array', 'items': {'type': 'integer'}}, 'depth': {'type': 'integer'}, 'save': S_SAVE}, ['ids']),
    ('save_advice', t_save_advice, '把 Claude 对某天的建议（Markdown）写进该天日报的「Claude 的建议」一节，覆盖旧建议。'
     '只写 本地 reports 目录，不碰游戏文件。',
     {'day': {'type': 'integer'}, 'text': {'type': 'string'}}, ['day', 'text']),
    ('journal', t_journal, '决策日志：给 text 时追加一条（自动标当前天数），记录玩家实际做了什么、结果如何、学到的规律；'
     '不给 text 时读最近 last 条。跨会话靠它延续上下文。',
     {'text': {'type': 'string'}, 'last': {'type': 'integer'}}, []),
    ('goals', t_goals, '读本局目标（goals.json）；给 update 对象时合并更新，如 {"protect": ["梅姬", "法图娜"]}。',
     {'update': {'type': 'object'}}, []),
    ('kb_search', t_kb_search, '知识库检索（先用它，比 search/config 快且准）：人工经验种子 + 全部仪式/事件/卡牌/结局的人话转写'
     '（每格条件、每个结算分支、卡能放哪/从哪获得，⛔妻子的不满 ☠移除或结局 已标注）。'
     'kinds 可选 seed/rite/event/card/over；full=true 返回整篇。',
     {'query': {'type': 'string'}, 'kinds': {'type': 'array', 'items': {'type': 'string'}},
      'k': {'type': 'integer'}, 'full': {'type': 'boolean'}}, ['query']),
    ('kb_get', t_kb_get, '按 id 取知识库整篇文档，如 rite:5006585、card:2000680、seed:经验#3。', {'id': {'type': 'string'}}, ['id']),
    ('kb_add_seed', t_kb_add_seed, '把新学到的规律/踩过的坑写成一条人工经验种子（检索权重最高）。'
     '每次查明一个容易出错的机制、或用户纠正了 Claude，就记一条。title 要能被关键词搜到。',
     {'title': {'type': 'string'}, 'text': {'type': 'string'}, 'file': {'type': 'string'}}, ['title', 'text']),
    ('kb_rebuild', t_kb_rebuild, '强制重建知识库索引（平时会按配置和种子文件的修改时间自动重建）。', {}, []),
]
TOOL_FUNCS = {name: fn for name, fn, *_ in TOOLS}
# 只写 本地状态目录 里自己的文件（日报、建议、日志、目标），游戏文件一律不碰
WRITES_OWN_FILES = {'daily_report', 'save_advice', 'journal', 'goals', 'kb_add_seed', 'kb_rebuild'}


def tool_list():
    return [{'name': n, 'description': desc,
             'inputSchema': {'type': 'object', 'properties': props, 'required': req},
             'annotations': {'readOnlyHint': n not in WRITES_OWN_FILES}}
            for n, _, desc, props, req in TOOLS]


_LIB_MODULES = ('jsonc', 'conditions', 'gamedata', 'report', 'kb')  # 按依赖顺序
_LIB_MTIMES = {}


def _hot_reload():
    """库模块（不含 server.py 本身）改动后自动重载，长期运行的会话不用重启也能用上新逻辑。
    新增/改名的工具仍要重启会话才会出现在工具列表里。"""
    import importlib
    global game, Game, SPECIAL_COUNTERS, select_path
    here = Path(__file__).resolve().parent
    mt = {m: (here / f'{m}.py').stat().st_mtime for m in _LIB_MODULES}
    if not _LIB_MTIMES:
        _LIB_MTIMES.update(mt)
        return
    if mt == _LIB_MTIMES:
        return
    for m in _LIB_MODULES:
        importlib.reload(sys.modules[m])
    _LIB_MTIMES.update(mt)
    gd = sys.modules['gamedata']
    Game, SPECIAL_COUNTERS, select_path = gd.Game, gd.SPECIAL_COUNTERS, gd.select_path
    game = Game()
    print('hot-reloaded: ' + ', '.join(_LIB_MODULES), file=sys.stderr)


def call_tool(name, args):
    try:
        _hot_reload()
    except Exception:
        traceback.print_exc(file=sys.stderr)
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return {'content': [{'type': 'text', 'text': f'未知工具 {name}'}], 'isError': True}
    if name != 'daily_report' and not name.startswith('kb_'):
        _ensure_today_report()
    try:
        result = fn(**(args or {}))
        text = json.dumps(result, ensure_ascii=False, indent=1)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f'\n…（输出超过 {MAX_CHARS} 字符被截断，用 path/query/limit 缩小范围）'
        return {'content': [{'type': 'text', 'text': text}], 'isError': 'error' in result}
    except Exception as e:
        return {'content': [{'type': 'text', 'text': f'{type(e).__name__}: {e}'}], 'isError': True}


# ---------------- JSON-RPC over stdio ----------------

def handle(msg):
    method, mid = msg.get('method'), msg.get('id')
    if mid is None:
        return None  # 通知
    if method == 'initialize':
        result = {'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'sultan', 'version': kb.KB_VERSION},
                  'instructions': '《苏丹的游戏》陪玩工具（不改游戏文件）：每天先 daily_report 读日报和 journal 读决策日志，'
                                  '逐项处理后用 save_advice 写建议；查规则、剧情、卡牌用途先用 kb_search，'
                                  '关键决定前用 config 读原文核对。'}
    elif method == 'tools/list':
        result = {'tools': tool_list()}
    elif method == 'tools/call':
        p = msg.get('params') or {}
        result = call_tool(p.get('name'), p.get('arguments'))
    elif method == 'ping':
        result = {}
    else:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'不支持 {method}'}}
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def main():
    stdin = open(sys.stdin.fileno(), encoding='utf-8', errors='replace', closefd=False)
    stdout = open(sys.stdout.fileno(), 'w', encoding='utf-8', newline='\n', closefd=False)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            reply = handle(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            reply = {'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': -32603, 'message': 'internal error'}}
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + '\n')
            stdout.flush()


if __name__ == '__main__':
    if len(sys.argv) > 1:
        # 调试：python server.py overview '{"save":"auto"}'
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        sys.stdout.reconfigure(encoding='utf-8')
        print(call_tool(sys.argv[1], args)['content'][0]['text'])
    else:
        main()
