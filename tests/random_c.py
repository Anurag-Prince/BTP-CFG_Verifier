"""Random structured C programs, used to test the IR reader on inputs nobody wrote by hand.

Two modes:
    supported  only the constructs of the regression programs (sequence, if, if-else, else-if, while, for,
               do-while, break, continue, return). Every condition is a single int comparison.
    extended   adds switch, &&, ||, !, ?:, goto and dead code after a jump. These are not supported for
               verification yet, but the READER must still read them correctly.

The generator also counts what it emitted (decisions and loops), so tests can check the reader's output
against the source, not only against LLVM.
Jump statements (break/continue/return) are only placed inside an `if` without `else`. That way every loop body
can reach the loop's latch, and every loop statement gives exactly one natural loop.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

CMP = ["<", "<=", ">", ">=", "==", "!="]
VARS = ["a", "b", "c", "x", "y"]


@dataclass
class Program:
    source: str
    decisions: dict[str, int]   # function -> number of conditions emitted (supported mode only)
    loops: dict[str, int]       # function -> number of loop statements emitted


class _Gen:
    def __init__(self, seed: int, extended: bool):
        self.r = random.Random(seed)
        self.extended = extended
        self.loop_var = 0
        self.decisions = 0
        self.loops = 0
        self.uses_goto = False

    def operand(self) -> str:
        return self.r.choice(VARS) if self.r.random() < 0.7 else str(self.r.randint(-5, 20))

    def expr(self) -> str:
        e = f"{self.r.choice(VARS)} {self.r.choice('+-*')} {self.operand()}"
        if self.extended and self.r.random() < 0.15:
            e = f"({self.simple_cond()} ? {e} : {self.operand()})"
        return e

    def simple_cond(self) -> str:
        return f"{self.r.choice(VARS)} {self.r.choice(CMP)} {self.operand()}"

    def cond(self) -> str:
        self.decisions += 1
        c = self.simple_cond()
        if self.extended:
            roll = self.r.random()
            if roll < 0.2:
                c = f"{c} && {self.simple_cond()}"
            elif roll < 0.35:
                c = f"({c}) || !({self.simple_cond()})"
        return c

    def block(self, depth: int, in_loop: bool, ind: str) -> list[str]:
        out: list[str] = []
        for _ in range(self.r.randint(1, 3)):
            out += self.stmt(depth, in_loop, ind)
        return out

    def stmt(self, depth: int, in_loop: bool, ind: str) -> list[str]:
        r = self.r
        kinds = ["assign", "assign"]
        if depth < 3:
            kinds += ["if", "if_else", "else_if", "while", "for", "do", "if_jump"]
            if self.extended:
                kinds += ["switch", "goto"]
        kind = r.choice(kinds)
        inner = ind + "    "
        if kind == "assign":
            return [f"{ind}{r.choice(['x', 'y'])} = {self.expr()};"]
        if kind == "if":
            return [f"{ind}if ({self.cond()}) {{", *self.block(depth + 1, in_loop, inner), f"{ind}}}"]
        if kind == "if_else":
            return [f"{ind}if ({self.cond()}) {{", *self.block(depth + 1, in_loop, inner),
                    f"{ind}}} else {{", *self.block(depth + 1, in_loop, inner), f"{ind}}}"]
        if kind == "else_if":
            return [f"{ind}if ({self.cond()}) {{", *self.block(depth + 1, in_loop, inner),
                    f"{ind}}} else if ({self.cond()}) {{", *self.block(depth + 1, in_loop, inner),
                    f"{ind}}} else {{", *self.block(depth + 1, in_loop, inner), f"{ind}}}"]
        if kind == "while":
            self.loops += 1
            return [f"{ind}while ({self.cond()}) {{", *self.block(depth + 1, True, inner),
                    f"{inner}x = x + 1;", f"{ind}}}"]
        if kind == "for":
            self.loops += 1
            self.decisions += 1
            v = f"i{self.loop_var}"
            self.loop_var += 1
            return [f"{ind}for (int {v} = 0; {v} < {r.choice(VARS)}; {v}++) {{",
                    *self.block(depth + 1, True, inner), f"{ind}}}"]
        if kind == "do":
            self.loops += 1
            body = self.block(depth + 1, True, inner)
            return [f"{ind}do {{", *body, f"{ind}}} while ({self.cond()});"]
        if kind == "if_jump":
            jumps = ["return x;"] + (["break;", "continue;"] if in_loop else [])
            jump = r.choice(jumps)
            lines = [f"{ind}if ({self.cond()}) {{"]
            if r.random() < 0.5:
                lines += self.block(depth + 1, in_loop, inner)
            lines.append(f"{inner}{jump}")
            if self.extended and r.random() < 0.3:
                lines.append(f"{inner}y = y - 1;  /* dead code after a jump */")
            return lines + [f"{ind}}}"]
        if kind == "switch":
            lines = [f"{ind}switch ({r.choice(VARS)} % 4) {{"]
            for value in r.sample(range(-1, 4), r.randint(1, 3)):
                lines.append(f"{ind}case {value}:")
                lines.append(f"{inner}{r.choice(['x', 'y'])} = {self.expr()};")
                if r.random() < 0.7:
                    lines.append(f"{inner}break;")
            if r.random() < 0.6:
                lines += [f"{ind}default:", f"{inner}y = {self.expr()};"]
            return lines + [f"{ind}}}"]
        if kind == "goto":
            self.uses_goto = True
            return [f"{ind}if ({self.cond()}) goto out;"]
        raise AssertionError(kind)


def random_program(seed: int, extended: bool = False, functions: int = 3) -> Program:
    lines, decisions, loops = [], {}, {}
    for k in range(functions):
        g = _Gen(seed * 1000 + k, extended)
        name = f"f{k}"
        body = g.block(0, False, "    ")
        lines += [f"int {name}(int a, int b, int c) {{", "    int x = a;", "    int y = b;", *body]
        if g.uses_goto:
            lines.append("out:")
        lines += ["    return x + y;", "}", ""]
        decisions[name], loops[name] = g.decisions, g.loops
    return Program("\n".join(lines), decisions, loops)


if __name__ == "__main__":  # print one program, e.g. `python tests/random_c.py 7 extended`
    import sys

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(random_program(seed, extended="extended" in sys.argv).source)
