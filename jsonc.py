"""苏丹的游戏配置用的 JSONC 解析器。

游戏配置带 // 和 /* */ 注释、尾逗号，还有大量重复键（例如 "any" 里并列多个 "all"、
"on" 里多个 "rite_end"）。json.loads 遇到重复键只保留最后一个，会悄悄丢掉分支。
这里把第 2 个及以后的同名键改名为 "键#2"、"键#3"……，顺序和内容都保留。
"""

DUP_SEP = '#'


class JSONCError(ValueError):
    pass


class _Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)

    def error(self, msg):
        line = self.s.count('\n', 0, self.i) + 1
        raise JSONCError(f'{msg}（第 {line} 行）')

    def skip(self):
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in ' \t\r\n﻿':
                self.i += 1
            elif c == '/' and self.i + 1 < n and s[self.i + 1] == '/':
                j = s.find('\n', self.i)
                self.i = n if j < 0 else j + 1
            elif c == '/' and self.i + 1 < n and s[self.i + 1] == '*':
                j = s.find('*/', self.i + 2)
                self.i = n if j < 0 else j + 2
            else:
                break

    def value(self):
        self.skip()
        if self.i >= self.n:
            self.error('意外的文件结尾')
        c = self.s[self.i]
        if c == '{':
            return self.obj()
        if c == '[':
            return self.arr()
        if c == '"':
            return self.string()
        return self.scalar()

    def obj(self):
        self.i += 1
        out = {}
        seen = {}
        while True:
            self.skip()
            if self.i >= self.n:
                self.error('对象未闭合')
            if self.s[self.i] == '}':
                self.i += 1
                return out
            if self.s[self.i] == ',':
                self.i += 1
                continue
            if self.s[self.i] != '"':
                self.error('应为字符串键')
            key = self.string()
            self.skip()
            if self.i >= self.n or self.s[self.i] != ':':
                self.error(f'键 {key!r} 后应为冒号')
            self.i += 1
            val = self.value()
            if key in seen:
                seen[key] += 1
                key = f'{key}{DUP_SEP}{seen[key]}'
            else:
                seen[key] = 1
            out[key] = val

    def arr(self):
        self.i += 1
        out = []
        while True:
            self.skip()
            if self.i >= self.n:
                self.error('数组未闭合')
            if self.s[self.i] == ']':
                self.i += 1
                return out
            if self.s[self.i] == ',':
                self.i += 1
                continue
            out.append(self.value())

    def string(self):
        s = self.s
        self.i += 1
        parts = []
        while True:
            j = self.i
            while j < self.n and s[j] != '"' and s[j] != '\\':
                j += 1
            if j >= self.n:
                self.error('字符串未闭合')
            parts.append(s[self.i:j])
            if s[j] == '"':
                self.i = j + 1
                return ''.join(parts)
            esc = s[j + 1] if j + 1 < self.n else ''
            if esc == 'u':
                parts.append(chr(int(s[j + 2:j + 6], 16)))
                self.i = j + 6
            else:
                parts.append({'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f'}.get(esc, esc))
                self.i = j + 2

    def scalar(self):
        s = self.s
        j = self.i
        while j < self.n and s[j] not in ',}] \t\r\n/':
            j += 1
        tok = s[self.i:j]
        self.i = j
        if tok == 'true':
            return True
        if tok == 'false':
            return False
        if tok == 'null':
            return None
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            self.error(f'无法识别的值 {tok!r}')


def loads(text):
    p = _Parser(text)
    v = p.value()
    p.skip()
    return v


def load(path):
    with open(path, encoding='utf-8-sig') as f:
        return loads(f.read())


def base_key(key):
    """'all#3' -> 'all'；不带后缀的键原样返回。"""
    k, sep, tail = key.rpartition(DUP_SEP)
    return k if sep and tail.isdigit() else key
