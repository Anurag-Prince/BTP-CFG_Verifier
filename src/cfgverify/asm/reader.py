"""Assembly text -> labels, directives and instructions.

Responsibility
    Tokenise an assembly file into a line-numbered stream of Label / Directive / Instruction items,
    strip comments, and split the stream into functions. All syntax details (comment characters,
    label forms, function-boundary directives) come from the selected ISA/dialect plug-in.
Keeps
    Line numbers, so every finding can point to exact assembly lines.
    `.loc` directives as optional source-line hints ([B5]).
Status
    STUB (Part 3). Blocked by [A1], [A2], [A5].
"""
