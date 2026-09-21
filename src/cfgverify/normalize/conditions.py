"""Part 6, layer 2: **branch conditions in a comparable form**.

The two sides describe the same test in different words:

    C          `while (i <= n)`
    LLVM IR    `%cmp = icmp sle i32 %0, %1`   then  `br i1 %cmp, label %body, label %end`
    x86-64     `cmpl -4(%rbp), %eax`          then  `jg .LBB0_3`      (jumps when NOT i <= n)
    AArch64    `cmp w8, w9`                   then  `b.gt .LBB0_3`
    RISC-V     `blt a1, a0, .LBB0_3`          (the comparison is inside the branch)

Reduced to one form, the TRUE edge of that branch is in every case:

    Condition(relation="le", signedness="s", shape=("var", "var"), polarity=True)

Three things are normalized:

1. **The relation name.** LLVM writes `sle`/`ugt`/`eq`; our assembly plug-ins already write `le.s`/`gt.u`/`eq`.
   Both become `(relation, signedness)`.
2. **Polarity.** A compiler is free to emit the opposite test and swap the targets (`jg` over the false arm
   instead of `jle` over the true arm). Every edge therefore carries both its own condition and the negation,
   and `polarity_free_key()` is the form that ignores the choice — normalization rule **N6**.
3. **Operand order.** `a < b` and `b > a` are the same test. Operands are reduced to a *shape*
   (`var` / `const:<value>` / `zero`), and when the shape is ordered the smaller spelling comes first, with the
   relation mirrored to match. AT&T assembly also writes `cmp src, dst` while computing `dst - src`, so the x86
   plug-in declares `swaps_compare_operands` and that is undone here.

What is deliberately *not* done: deciding that two `var` operands are the *same* variable. That needs a value
mapping between an SSA form and a register file, which is matcher level **L4** and depends on **[C1]**. The
shape is what can be compared soundly today; `docs/NORMALIZATION.md` says so plainly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: LLVM predicate -> (relation, signedness). Signedness: "s" signed, "u" unsigned, "f" float, "" none.
_LLVM = {
    "eq": ("eq", ""), "ne": ("ne", ""),
    "slt": ("lt", "s"), "sle": ("le", "s"), "sgt": ("gt", "s"), "sge": ("ge", "s"),
    "ult": ("lt", "u"), "ule": ("le", "u"), "ugt": ("gt", "u"), "uge": ("ge", "u"),
    "oeq": ("eq", "f"), "one": ("ne", "f"), "olt": ("lt", "f"), "ole": ("le", "f"),
    "ogt": ("gt", "f"), "oge": ("ge", "f"),
    "ueq": ("eq", "f"), "une": ("ne", "f"), "ult.f": ("lt", "f"), "ule.f": ("le", "f"),
    "ord": ("ord", "f"), "uno": ("uno", "f"),
}
#: The assembly plug-ins already emit `lt.s`, `ge.u`, `eq`, and the zero tests `eq0`/`ne0`/`lt0`/`ge0`.
_RELATIONS = ("eq", "ne", "lt", "le", "gt", "ge", "sign", "nosign", "overflow", "nooverflow",
              "parity", "noparity", "ord", "uno")
_NEGATION = {"eq": "ne", "ne": "eq", "lt": "ge", "ge": "lt", "gt": "le", "le": "gt",
             "sign": "nosign", "nosign": "sign", "overflow": "nooverflow", "nooverflow": "overflow",
             "parity": "noparity", "noparity": "parity", "ord": "uno", "uno": "ord"}
_MIRROR = {"eq": "eq", "ne": "ne", "lt": "gt", "gt": "lt", "le": "ge", "ge": "le"}

_CONST = re.compile(r"^[$#]?[-+]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)$")


def parse_predicate(predicate: str | None) -> tuple[str | None, str]:
    """Any side's predicate spelling -> (relation, signedness). Unknown spellings return (None, '')."""
    if not predicate:
        return None, ""
    p = predicate.strip().lower()
    if p in _LLVM:
        return _LLVM[p]
    if p in ("bit0", "bit1"):                             # tbz/tbnz: resolved by _sign_bit_test
        return p, ""
    if p.endswith("0") and p[:-1] in _RELATIONS:          # cbz/bnez style: "eq0" is "== 0"
        return p[:-1], ""
    head, _, tail = p.partition(".")
    if head in _RELATIONS and tail in ("", "s", "u", "f"):
        return head, tail
    return None, ""


def operand_shape(operand: str | None) -> str:
    """`var`, `zero`, or `const:<value>`. Registers, SSA names and memory operands are all just `var`."""
    if operand is None:
        return "var"
    text = operand.strip()
    if _CONST.match(text):
        value = int(text.lstrip("$#"), 0)
        return "zero" if value == 0 else f"const:{value}"
    if text in ("true", "false"):
        return f"const:{int(text == 'true')}"
    return "var"


@dataclass(frozen=True)
class Condition:
    """One branch condition in comparable form."""
    relation: str | None                  # eq ne lt le gt ge ... or None when it could not be recovered
    signedness: str = ""                  # s | u | f | ""
    shape: tuple[str, ...] = ()           # operand shapes, after mirroring
    polarity: bool = True                 # True: this edge is taken when the relation holds
    zero_test: bool = False               # the branch tests one value against zero (cbz, bnez, test x,x)

    # ---------------------------------------------------------------------------------------- forms
    @property
    def key(self) -> str:
        """The comparable key of this edge, polarity included."""
        relation = self.relation or "?"
        name = f"{relation}.{self.signedness}" if self.signedness else relation
        return f"{'' if self.polarity else '!'}{name}({', '.join(self.shape)})"

    def negated(self) -> "Condition":
        relation = _NEGATION.get(self.relation or "", self.relation)
        return Condition(relation, self.signedness, self.shape, not self.polarity, self.zero_test)

    def equivalence_class(self) -> set[str]:
        """Every spelling of this test that cannot be told apart once the arms and operands are unknown.

        A compiler may emit the opposite test and swap the arms (`jg` over the false arm instead of `jle`
        over the true one), and — when the two operands have the same shape — it may also compare them the
        other way round. So `i <= n` in the IR, `jg` after `cmp` on x86 and `blt n, i` on RISC-V are one test.
        """
        forms = {self, self.negated()}
        if len(self.shape) == 2 and self.shape[0] == self.shape[1]:
            forms |= {Condition(_MIRROR.get(f.relation or "", f.relation), f.signedness, f.shape,
                                f.polarity, f.zero_test) for f in list(forms)}
        return {f.key.lstrip("!") for f in forms}

    def polarity_free_key(self) -> str:
        """Rule **N6**: one representative of `equivalence_class()`, so both sides produce the same string."""
        return min(self.equivalence_class())

    def __str__(self) -> str:
        return self.key


def _mirror_if_needed(relation: str | None, shapes: list[str]) -> tuple[str | None, tuple[str, ...]]:
    """`a < b` and `b > a` are one test: order the operand shapes and mirror the relation to match.

    When the two shapes are *equal* (the usual `var` vs `var`) nothing distinguishes the operands, so the
    ordering has nothing to work with and the relation is left as written. `Condition.key` therefore keeps the
    direction, and it is `polarity_free_key()` — the form rule N6 compares — that collapses the spellings
    that cannot be told apart. Recovering the difference needs a value mapping between SSA names and
    registers, which is matcher level **L4** and depends on **[C1]**.
    """
    if len(shapes) != 2 or relation not in _MIRROR:
        return relation, tuple(shapes)
    if shapes[0] <= shapes[1]:
        return relation, tuple(shapes)
    return _MIRROR[relation], (shapes[1], shapes[0])


def _sign_bit_test(relation: str | None, operands: list[str]) -> tuple[str | None, list[str]] | None:
    """`tbnz w8, #31` / `tbz x0, #63` test the sign bit, which is `< 0` / `>= 0`.

    A compiler is free to test a value's sign that way instead of comparing it with zero, and AArch64 does it
    constantly. Without this, the same C `if (x < 0)` would produce `lt.s(var, zero)` on one side and an
    unrepresentable condition on the other — exactly the kind of accident Part 6 exists to remove.
    """
    if relation not in ("bit0", "bit1") or len(operands) != 2:
        return None
    shape = operand_shape(operands[1])
    if shape in ("const:31", "const:63"):                  # the sign bit of a 32- or 64-bit value
        return ("lt" if relation == "bit1" else "ge"), [operands[0], "0"]
    return f"bit{'set' if relation == 'bit1' else 'clear'}:{shape}", [operands[0], "0"]


def from_branch(branch, *, swap_operands: bool = False) -> Condition:
    """Build a Condition from a `model.cfg.BranchInfo` (either side) and the edge's own polarity.

    `branch.predicate` is the relation; the operands come from `branch.compare`, which both builders write in
    the same shape: `CMP.<predicate> <operand>, <operand>` (the HLL side prefixes it with `v3 = `).
    `swap_operands` undoes AT&T's `cmp src, dst` order.
    """
    if branch is None:
        return Condition(None)
    polarity = not str(branch.taken_when or "").endswith("false")
    relation, signedness = parse_predicate(branch.predicate)

    operands: list[str] = []
    compare = branch.compare or ""
    if compare:
        body = compare.split(" = ", 1)[-1]           # drop the HLL side's "v3 = " result prefix
        _, _, arguments = body.partition(" ")        # drop the "CMP.<predicate>" head
        operands = [o.strip() for o in arguments.split(",") if o.strip()]
    sign_test = _sign_bit_test(relation, operands)
    if sign_test:
        relation, operands = sign_test
        signedness = "s" if relation in ("lt", "ge") else signedness
    elif len(operands) > 2:
        # A flag-setting arithmetic instruction written destination-first (AArch64 `subs w8, w8, w9`,
        # RISC-V `slt a0, a1, a2`): the comparison is between its last two operands.
        operands = operands[-2:]
    if swap_operands and len(operands) == 2:
        # AT&T writes `cmp src, dst` and computes `dst - src`, so the operands read backwards.
        operands = [operands[1], operands[0]]

    shapes = [operand_shape(o) for o in operands]
    zero_test = False
    if not shapes:                                   # a branch whose condition could not be recovered
        shapes = ["var"]
    if len(shapes) == 1:                             # cbz/bnez and friends: an implicit comparison with zero
        shapes = [shapes[0], "zero"]
        zero_test = True
    if len(shapes) == 2 and "zero" in shapes:
        zero_test = True

    relation, shape = _mirror_if_needed(relation, shapes)
    condition = Condition(relation, signedness, shape, True, zero_test)
    return condition if polarity else condition.negated()


def condition_of_edge(cfg, edge) -> Condition:
    """The comparable condition of one edge, or a Condition(None) when the edge is not conditional."""
    if edge.kind in ("T", "F"):
        # AT&T assembly writes `cmp src, dst` but computes `dst - src`, so its operands read backwards.
        swap = cfg.origin == "asm" and cfg.meta.get("syntax") == "att"
        return from_branch(edge.branch, swap_operands=swap)
    if edge.kind.startswith("case:") or edge.kind == "default":
        value = edge.kind.split(":", 1)[1] if ":" in edge.kind else None
        return Condition("eq", "", ("var", operand_shape(value) if value else "default"), True)
    return Condition(None)


def decision_key(cfg, node_id: str, *, polarity_free: bool = False) -> str:
    """The comparable description of a whole decision: its condition and the order of its arms."""
    edges = cfg.out_edges(node_id)
    if len(edges) < 2:
        return ""
    conditions = [condition_of_edge(cfg, e) for e in edges]
    if polarity_free:
        return " | ".join(c.polarity_free_key() for c in conditions[:1])
    return " | ".join(f"{e.kind}:{c.key}" for e, c in zip(edges, conditions))
