"""读取《苏丹的游戏》存档与（叠加 mod 后的）配置。全部只读。"""

import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import jsonc

def _default_game():
    steam = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Steam' / 'steamapps'
    for apps in (steam, Path('D:/SteamLibrary/steamapps')):
        candidate = apps / 'common' / "Sultan's Game"
        if candidate.is_dir():
            return candidate
    return steam / 'common' / "Sultan's Game"


GAME = Path(os.environ['SULTAN_GAME_DIR']) if os.getenv('SULTAN_GAME_DIR') else _default_game()
WORKSHOP = Path(os.getenv('SULTAN_WORKSHOP_DIR') or GAME.parent.parent / 'workshop' / 'content' / '3117820')
SAVEROOT = Path(os.environ.get('SULTAN_SAVE_DIR',
                               Path(os.environ.get('USERPROFILE', '')) / r'AppData\LocalLow\DoubleCross\SultansGame\SAVEDATA'))
BASE_CONFIG = GAME / "Sultan's Game_Data" / 'StreamingAssets' / 'config'

# 存档里的这些计数器在 ui.json 里有显示名
SPECIAL_COUNTERS = ['7100001', '7100002', '7100003', '7100004', '7100005', '7100006', '7100007', '7100008']
# 配置子目录里每个文件就是一条记录，文件名 = id
DIR_KINDS = ('rite', 'event', 'loot', 'after_story', 'rite_template', 'init', 'dt', 'wizard')
# 单文件、按顶层键合并的配置
FILE_KINDS = {'card': 'cards.json', 'tag': 'tag.json', 'over': 'over.json', 'quest': 'quest.json',
              'upgrade': 'upgrade.json', 'achievement': 'achievement.json'}
KIND_ALIASES = {'cards': 'card', 'tags': 'tag', 'rites': 'rite', 'events': 'event', 'ending': 'over'}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# 存档会给替身和主角显示同名，用卡牌配置 ID 区分。
CARD_ALIASES = {
    '2000861': ('秘密双胞胎', '替身与主角同名，装备可以更换。以卡牌配置 ID 辨认，死亡会直接结束游戏。'),
}

# 描述物品本身的标签，装备时不加到穿戴者身上
EQUIP_META_TAGS = {'装备', '已装备', '饰品', '武器', '服装', '坐骑', '纯色材质', '已拥有', '装备商人', '武器存货',
                   '可堆叠', '消耗品', '动物', '读物', '情报', '思潮', 'adsorb_spec', 'pic'}


FRESH_SECONDS = 2.0  # 文件是否更新的检查结果在这段时间内复用，一份日报会查几万次卡牌配置


class Game:
    def __init__(self):
        self._file_cache = {}      # path -> (mtime, parsed)
        self._merged = {}          # kind -> (signature, dict)
        self._dir_index = {}       # kind -> (signature, {id: [paths...]})
        self._names = {}           # (kind, id) -> name
        self._counter_names = None
        self._fresh_cache = {}     # key -> (time, value)

    def _fresh(self, key, fn):
        hit = self._fresh_cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < FRESH_SECONDS:
            return hit[1]
        v = fn()
        self._fresh_cache[key] = (now, v)
        return v

    # ---------- 存档 ----------
    def save_dir(self):
        return self._fresh('save_dir', self._find_save_dir)

    def _find_save_dir(self):
        if not SAVEROOT.is_dir():
            raise FileNotFoundError(f'找不到存档目录 {SAVEROOT}，请检查 SULTAN_SAVE_DIR')
        if (SAVEROOT / 'auto_save.json').is_file():
            return SAVEROOT
        folders = [SAVEROOT, *(p for p in SAVEROOT.iterdir() if p.is_dir())]
        autos = [d / 'auto_save.json' for d in folders if (d / 'auto_save.json').is_file()]
        saves = autos or [p for d in folders for p in self._round_files(d)]
        if not saves:
            raise FileNotFoundError(f'在 {SAVEROOT} 下找不到自动或回合存档')
        return max(saves, key=lambda p: p.stat().st_mtime_ns).parent

    @staticmethod
    def _round_files(directory):
        return (p for p in directory.glob('round_*.json')
                if re.fullmatch(r'round_[0-9]+(?:_end)?\.json', p.name) and p.is_file())

    def save_path(self, which='auto'):
        d = self.save_dir()
        which = str(which).strip()
        if which in ('', 'auto', 'auto_save', 'latest'):
            auto = d / 'auto_save.json'
            if auto.is_file():
                return auto
            return max(self._round_files(d), key=lambda p: p.stat().st_mtime_ns)
        m = re.fullmatch(r'(?:round_)?(\d+)(_end|end|e)?(?:\.json)?', which)
        if not m:
            raise ValueError(f'无法识别的存档名 {which!r}，用 auto、74、74_end 这样的写法')
        name = f'round_{m[1]}' + ('_end' if m[2] else '') + '.json'
        p = d / name
        if not p.exists():
            raise FileNotFoundError(f'没有 {name}')
        return p

    def load_save(self, which='auto'):
        p = self.save_path(which)
        return self._fresh(('save', str(p)),
                           lambda: self._cached(p, lambda: json.loads(p.read_text(encoding='utf-8-sig'))))

    def list_saves(self):
        d = self.save_dir()
        out = []
        for p in d.glob('*.json'):
            if p.name.startswith('round_') or p.name == 'auto_save.json':
                out.append((p.stat().st_mtime, p.name))
        out.sort(reverse=True)
        return [n for _, n in out]

    def global_save(self):
        p = self.save_dir() / 'global.json'
        return self._cached(p, lambda: json.loads(p.read_text(encoding='utf-8-sig')))

    # ---------- mod 叠加 ----------
    def mod_dirs(self):
        """按游戏加载顺序返回已启用 mod 的 config 目录（后面的覆盖前面的）。"""
        return self._fresh('mod_dirs', self._find_mod_dirs)

    def _find_mod_dirs(self):
        try:
            mods = self.global_save().get('mods', [])
        except Exception:
            mods = []
        out = []
        for m in mods:
            mid = str(m).split(':')[-1]
            if not mid or mid in ('.', '..') or Path(mid).name != mid or Path(mid).drive:
                continue
            d = WORKSHOP / mid / 'config'
            if d.is_dir():
                out.append((mid, d))
        return out

    def mod_names(self):
        out = []
        for mid, d in self.mod_dirs():
            info = next((p for p in (d.parent / 'info.json', d.parent / 'Info.json') if p.exists()), None)
            name = mid
            if info:
                try:
                    name = jsonc.load(info).get('name', mid)
                except Exception:
                    pass
            out.append({'id': mid, 'name': name})
        return out

    def layers(self):
        return [('base', BASE_CONFIG)] + self.mod_dirs()

    def _cached(self, path, loader):
        path = Path(path)
        mt = path.stat().st_mtime
        hit = self._file_cache.get(path)
        if hit and hit[0] == mt:
            return hit[1]
        v = loader()
        self._file_cache[path] = (mt, v)
        return v

    def parse(self, path):
        return self._cached(path, lambda: jsonc.load(path))

    def _signature(self, rel):
        def compute():
            sig = []
            for src, d in self.layers():
                p = d / rel
                if p.exists():
                    sig.append((src, p.stat().st_mtime))
            return tuple(sig)
        return self._fresh(('sig', rel), compute)

    def merged(self, kind):
        """单文件配置：各层按顶层键合并。返回 (dict, {key: 来源})。"""
        rel = FILE_KINDS[kind]
        sig = self._signature(rel)
        hit = self._merged.get(kind)
        if hit and hit[0] == sig:
            return hit[1]
        data, origin = {}, {}
        for src, d in self.layers():
            p = d / rel
            if p.exists():
                for k, v in self.parse(p).items():
                    data[k] = v
                    origin[k] = src
        self._merged[kind] = (sig, (data, origin))
        return data, origin

    def dir_index(self, kind):
        """目录型配置：id -> [(来源, 路径)...]，最后一个生效。"""
        sig = self._fresh(('dirsig', kind), lambda: tuple(
            (src, d.joinpath(kind).stat().st_mtime) for src, d in self.layers() if d.joinpath(kind).is_dir()))
        hit = self._dir_index.get(kind)
        if hit and hit[0] == sig:
            return hit[1]
        idx = {}
        for src, d in self.layers():
            sub = d / kind
            if sub.is_dir():
                for p in sub.rglob('*.json'):
                    idx.setdefault(p.stem, []).append((src, p))
        self._dir_index[kind] = (sig, idx)
        return idx

    def norm_kind(self, kind):
        kind = KIND_ALIASES.get(kind, kind)
        if kind not in FILE_KINDS and kind not in DIR_KINDS:
            raise ValueError(f'未知类别 {kind!r}，可用：{", ".join(list(FILE_KINDS) + list(DIR_KINDS))}')
        return kind

    def get(self, kind, id_):
        """返回 (生效配置, 来源链)。来源链里最后一个是生效的。"""
        kind = self.norm_kind(kind)
        id_ = str(id_)
        if kind in FILE_KINDS:
            data, origin = self.merged(kind)
            if id_ not in data:
                return None, []
            chain = []
            for src, d in self.layers():
                p = d / FILE_KINDS[kind]
                if p.exists() and id_ in self.parse(p):
                    chain.append(src)
            return data[id_], chain
        entries = self.dir_index(kind).get(id_)
        if not entries:
            return None, []
        src, p = entries[-1]
        return self.parse(p), [f'{s}:{p}' for s, p in entries]

    def raw_text(self, kind, id_):
        kind = self.norm_kind(kind)
        if kind in FILE_KINDS:
            raise ValueError('单文件配置不提供整段原文，用 config 工具配合 path 参数取子树')
        entries = self.dir_index(kind).get(str(id_))
        if not entries:
            return None, None
        p = entries[-1][1]
        return p.read_text(encoding='utf-8-sig'), str(p)

    # ---------- 名字 ----------
    def card_cfg(self, cid):
        c = self.merged('card')[0].get(str(cid)) or {}
        alias = CARD_ALIASES.get(str(cid))
        if alias and c:
            c = {**c, 'name': alias[0], 'alias_note': alias[1]}
        return c

    def tag_table(self):
        return self.merged('tag')[0]

    def tag_name(self, code):
        t = self.tag_table().get(code)
        return t.get('name', code) if isinstance(t, dict) else code

    def name_of(self, kind, id_):
        kind = self.norm_kind(kind)
        if kind == 'card':
            return self.card_cfg(id_).get('name', str(id_))
        if kind in FILE_KINDS:
            v = self.merged(kind)[0].get(str(id_))
            return v.get('name', str(id_)) if isinstance(v, dict) else str(id_)
        key = (kind, str(id_))
        if key not in self._names:
            entries = self.dir_index(kind).get(str(id_))
            name = str(id_)
            if entries:
                text = entries[-1][1].read_text(encoding='utf-8-sig', errors='replace')[:4000]
                # 事件文件没有 name，用 text 当标题
                for field in (('text', 'name') if kind == 'event' else ('name', 'text')):
                    m = re.search(rf'"{field}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
                    if m:
                        name = m[1][:60]
                        break
            self._names[key] = name
        return self._names[key]

    def thing_name(self, id_):
        """按 id 号段猜类别取名字：2xxxxxx 卡，3xxxxxx 标签，5xxxxxx 仪式/事件。"""
        s = str(id_)
        if s.startswith('2'):
            return self.card_cfg(s).get('name', s)
        if s.startswith('50') and s in self.dir_index('rite'):
            return self.name_of('rite', s)
        if s.startswith('5') and s in self.dir_index('event'):
            return self.name_of('event', s)
        if s in self.dir_index('rite'):
            return self.name_of('rite', s)
        return s

    def counter_names(self):
        """从配置注释里收集计数器名字：如  "counter.7100003<":20, //权势"""
        if self._counter_names is not None:
            return self._counter_names
        votes = {}
        pat = re.compile(r'counter[.+\-=]?(\d{7})[^"]*"\s*:\s*[^,\n]*,?\s*//\s*([^\n]+)')
        for _, d in self.layers():
            for p in d.rglob('*.json'):
                text = p.read_text(encoding='utf-8-sig', errors='replace')
                if 'counter' not in text:
                    continue
                for m in pat.finditer(text):
                    note = m[2].strip().rstrip(',').strip()
                    if note:
                        votes.setdefault(m[1], Counter())[note[:40]] += 1
        names = {k: c.most_common(1)[0][0] for k, c in votes.items()}
        ui = BASE_CONFIG / 'ui.json'
        if ui.exists():
            uid = self.parse(ui)
            for k in SPECIAL_COUNTERS:
                v = uid.get(f'COUNTER_{k}')
                if isinstance(v, dict) and v.get('zhCN'):
                    names[k] = v['zhCN']
        self._counter_names = names
        return names

    # ---------- 卡牌 ----------
    def own_tags(self, card):
        """cards.json 的中文基础标签 + 存档里的英文 code 增量（不含装备加成）。"""
        out = dict(self.card_cfg(card['id']).get('tag') or {})
        for code, v in (card.get('tag') or {}).items():
            name = self.tag_name(code)
            if _num(v) is not None and _num(out.get(name)) is not None:
                out[name] = out[name] + v
            else:
                out[name] = v
        return out

    def equip_bonus(self, card):
        """身上装备带来的标签加成：如圣主像 魔力+10，枷锁 追随者-1，猫当装备给 贵族+1。"""
        bonus = {}
        for e in card.get('equips') or []:
            if not isinstance(e, dict):
                continue
            for k, v in self.own_tags(e).items():
                if k in EQUIP_META_TAGS or _num(v) is None or not v:
                    continue
                if k == '追随者' and v > 0:  # 猫自己是追随者，不等于穿戴者也变成追随者；枷锁的 -1 要保留
                    continue
                bonus[k] = bonus.get(k, 0) + v
        return bonus

    def effective_tags(self, card):
        """生效标签 = 自身标签 + 身上装备的加成（格子条件和检定都按这个算）。"""
        out = self.own_tags(card)
        for k, v in self.equip_bonus(card).items():
            out[k] = (out.get(k) or 0) + v if _num(out.get(k, 0)) is not None else v
        return out

    def owned(self, card, tags=None):
        tags = tags if tags is not None else self.effective_tags(card)
        cfg = self.card_cfg(card['id'])
        if cfg.get('type') == 'sudan':
            return True
        return any((_num(tags.get(t)) or 0) > 0 for t in ('已拥有', '追随者', '主角'))

    def remaining_days(self, card):
        cv = _num(self.card_cfg(card['id']).get('card_vanishing')) or 0
        return cv - card.get('life', 0) if cv > 0 else None

    def describe_card(self, card, full=False, where=None):
        cfg = self.card_cfg(card['id'])
        tags = self.effective_tags(card)
        d = {
            'uid': card['uid'], 'id': card['id'], 'name': card.get('custom_name') or cfg.get('name', card['id']),
            'type': cfg.get('type'), 'rare': cfg.get('rare', 0) + (card.get('rareup') or 0),
            'owned': self.owned(card, tags), 'life': card.get('life'),
        }
        if cfg.get('alias_note'):
            d['note'] = cfg['alias_note']
        if card.get('count', 1) != 1:
            d['count'] = card['count']
        rd = self.remaining_days(card)
        if rd is not None:
            d['days_left'] = rd
        if where:
            d['where'] = where
        # 非 full 时藏掉 tag.json 里没有的内部 code（pic、lock_105 之类）
        d['tags'] = {k: v for k, v in tags.items()
                     if k != '已拥有' and v not in (0, None) and (full or not re.fullmatch(r'[a-z_0-9]+', k))}
        if card.get('equips'):
            d['equips'] = [self.card_cfg(e['id']).get('name', e['id']) if isinstance(e, dict) else e for e in card['equips']]
        if full:
            d['title'] = cfg.get('title')
            d['text'] = cfg.get('text')
            if cfg.get('tips'):
                d['tips'] = cfg['tips']
            if cfg.get('post_rite'):
                d['post_rite'] = cfg['post_rite']
            if cfg.get('vanish'):
                d['vanish'] = cfg['vanish']
            d['save_tag_raw'] = card.get('tag')
            bonus = self.equip_bonus(card)
            if bonus:
                d['equip_bonus'] = bonus  # 已经算进 tags 里了，这里单列方便看来源
            d['equip_slots'] = card.get('equip_slots')
        return d

    def all_cards(self, save):
        """场上所有卡：手牌区 + 放在仪式格子里的 + 装备在人物身上的。返回 (card, 位置说明)。

        装备也是完整的卡牌对象（比如猫「贝姬夫人」可以装备在人身上一起上朝），
        漏掉它们会把被装备的人物误判成"离开"。
        """
        base = [(c, None) for c in save.get('cards', [])]
        for r in save.get('rites', []):
            rcfg, _ = self.get('rite', r['id'])
            slots = list((rcfg or {}).get('cards_slot', {}).keys())
            for i, c in enumerate(r.get('cards') or []):
                if c:
                    slot = slots[i] if i < len(slots) else f'#{i}'
                    base.append((c, f'仪式「{self.name_of("rite", r["id"])}」(uid {r["uid"]}) 格 {slot}'))
        out = []

        def add(c, where):
            out.append((c, where))
            for e in c.get('equips') or []:
                if isinstance(e, dict):
                    wearer = f"{self.card_cfg(c['id']).get('name', c['id'])}#{c['uid']}"
                    add(e, f'装备在{wearer}身上' + (f'（{where}）' if where else ''))
        for c, where in base:
            add(c, where)
        return out

    def inventory(self, save):
        """我方物品按名字汇总：手牌区的数量、放在仪式里的数量、装备在谁身上。"""
        groups = {}
        for c, where in self.all_cards(save):
            cfg = self.card_cfg(c['id'])
            if cfg.get('type') != 'item' or not self.owned(c):
                continue
            tags = self.own_tags(c)
            kind = next((k for k in ('装备', '情报', '思潮', '读物', '消耗品') if tags.get(k)), '其他')
            name = (cfg.get('name') or str(c['id'])).split('\n')[0]
            key = (kind, name, cfg.get('rare', 0))
            g = groups.setdefault(key, {'kind': kind, 'name': name, 'rare': cfg.get('rare', 0),
                                        'in_hand': 0, 'in_rites': 0, 'equipped_on': []})
            if kind == '装备' and 'effect' not in g:
                g['effect'] = {k: v for k, v in tags.items()
                               if k not in EQUIP_META_TAGS and _num(v) is not None and v
                               and not (k == '追随者' and v > 0)}
            n = c.get('count', 1)
            if not where:
                g['in_hand'] += n
            elif where.startswith('装备在'):
                g['equipped_on'].append(where[3:].split('身上')[0])
            else:
                g['in_rites'] += n
        order = {'装备': 0, '情报': 1, '思潮': 2, '读物': 3, '消耗品': 4, '其他': 5}
        return sorted(groups.values(), key=lambda g: (order[g['kind']], -g['rare'], g['name']))

    # ---------- 仪式 ----------
    def describe_rite(self, r, full=False):
        cfg, chain = self.get('rite', r['id'])
        cfg = cfg or {}
        d = {'uid': r['uid'], 'id': r['id'], 'name': r.get('custom_name') or cfg.get('name', r['id']),
             'location': cfg.get('location'), 'life': r.get('life'), 'started': r.get('start'),
             'round_number': cfg.get('round_number'), 'waiting_round': cfg.get('waiting_round')}
        if r.get('start'):
            d['start_round'] = r.get('start_round')
        slots = list(cfg.get('cards_slot', {}).items())
        placed = []
        for i, c in enumerate(r.get('cards') or []):
            if c:
                key = slots[i][0] if i < len(slots) else f'#{i}'
                placed.append({'slot': key, 'uid': c['uid'], 'name': self.card_cfg(c['id']).get('name', c['id'])})
        if placed:
            d['placed'] = placed
        if cfg.get('tips_text'):
            d['tips_text'] = cfg['tips_text']
        if full:
            d['text'] = cfg.get('text')
            d['slots'] = {k: {'text': v.get('text'), 'condition': v.get('condition'), 'is_key': v.get('is_key'),
                              'is_empty': v.get('is_empty'), 'is_enemy': v.get('is_enemy')}
                          for k, v in slots}
            d['open_conditions'] = cfg.get('open_conditions')
            d['config_source'] = chain
        return d

    # ---------- 搜索 ----------
    def search(self, query, kinds, limit=30):
        q = query.lower()
        hits = []
        for kind in kinds:
            kind = self.norm_kind(kind)
            if kind in FILE_KINDS:
                data, origin = self.merged(kind)
                for k, v in data.items():
                    if not isinstance(v, dict):
                        continue
                    blob = ' '.join(str(v.get(f, '')) for f in ('name', 'title', 'text', 'tips', 'code'))
                    if q in k.lower() or q in blob.lower():
                        hits.append({'kind': kind, 'id': k, 'name': v.get('name'),
                                     'snippet': _snippet(blob, q), 'source': origin.get(k)})
                        if len(hits) >= limit:
                            return hits
            else:
                for id_, entries in self.dir_index(kind).items():
                    src, p = entries[-1]
                    text = p.read_text(encoding='utf-8-sig', errors='replace')
                    if q in id_ or q in text.lower():
                        hits.append({'kind': kind, 'id': id_, 'name': self.name_of(kind, id_),
                                     'snippet': _snippet(text, q), 'source': src})
                        if len(hits) >= limit:
                            return hits
        return hits


def _snippet(text, q, width=60):
    i = text.lower().find(q)
    if i < 0:
        return text[:width].replace('\n', ' ')
    a = max(0, i - width // 2)
    return text[a:i + len(q) + width // 2].replace('\n', ' ').strip()


def select_path(obj, path):
    """按 'cards_slot.s3.condition' 这样的路径取子树；列表用数字下标。"""
    if not path:
        return obj
    cur = obj
    for part in path.split('.'):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            if part not in cur:
                raise KeyError(f'没有键 {part!r}，可选：{", ".join(list(cur)[:40])}')
            cur = cur[part]
        else:
            raise KeyError(f'{part!r} 之前已经是标量')
    return cur
