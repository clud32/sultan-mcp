"""格子条件的单卡判定（尽力而为）。

返回三态：True 满足 / False 不满足 / None 取决于别的格子、计数器或场上情况，单看这张卡判断不了。
语义：
- "贵族": 1        标签值 >= 1
- "!金币": 1       标签值 < 1（没有）
- "魔力>": 5       严格大于，即至少 6；同理 >= <= < =
- "type"/"is"/"rare" 分别比卡牌类型、卡牌 id、品级
- "cost.消耗品=": 1 当作标签 消耗品 处理
- any / all（含 any#2、all#3 这种重复键）按或 / 与组合
"""

import re

from jsonc import base_key

_OP = re.compile(r'^(!?)(.+?)(>=|<=|=|>|<)?$')
# 引用其他格子、公式、计数器、场上/手牌情况的键，单卡判断不了
_CONTEXT = re.compile(r'^!?(s\d+(\.|$)|f:|counter\.|global_counter\.|have\.|hand_have\.|table_have\.|rite|round)')


def _cmp(a, op, b):
    if op in (None, '>='):
        return a >= b
    return {'>': a > b, '<': a < b, '<=': a <= b, '=': a == b}[op]


def check_card(cond, card_type, card_id, rare, tags, count=1):
    """cond 是 cards_slot.sN.condition；tags 是生效标签（中文名 -> 值）；count 是堆叠数量。"""
    if not cond:
        return True
    if not isinstance(cond, dict):
        return None
    results = [_check_key(k, v, card_type, card_id, rare, tags, count) for k, v in cond.items()]
    return _all(results)


def _all(rs):
    if any(r is False for r in rs):
        return False
    return None if any(r is None for r in rs) else True


def _any(rs):
    if any(r is True for r in rs):
        return True
    return None if any(r is None for r in rs) else False


def _check_key(key, val, card_type, card_id, rare, tags, count=1):
    k = base_key(key)
    if k in ('any', 'all'):
        if not isinstance(val, dict):
            return None
        rs = [_check_key(kk, vv, card_type, card_id, rare, tags, count) for kk, vv in val.items()]
        return _any(rs) if k == 'any' else _all(rs)
    if _CONTEXT.match(k):
        return None
    m = _OP.match(k)
    neg, name, op = m[1] == '!', m[2], m[3]
    # cost.X 是从这一摞里扣 X，按 标签值 × 堆叠数量 算（如 "cost.金币": 3 要至少 3 枚金币）
    mult = 1
    if name.startswith('cost.'):
        name = name[5:]
        mult = max(count, 1)
        if op == '=':  # "cost.金币=": 5 是正好扣 5 枚，游戏会从一摞里拆，够数就行
            op = '>='
    if name == 'type':
        r = card_type == val
    elif name == 'is':
        r = str(card_id) == str(val)
    elif name == 'rare':
        if not isinstance(val, (int, float)):
            return None
        r = _cmp(rare, op, val)
    else:
        have = tags.get(name, 0)
        if not isinstance(have, (int, float)) or isinstance(have, bool):
            have = 1 if have else 0
        if not isinstance(val, (int, float)):
            return None
        r = _cmp(have * mult, op, val)
    return (not r) if neg else r


def walk_actions(node):
    """遍历结算里的 action/result（含 case 这类嵌套），产出 (键, 值)。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            if isinstance(v, (dict, list)):
                yield from walk_actions(v)
    elif isinstance(node, list):
        for x in node:
            yield from walk_actions(x)
