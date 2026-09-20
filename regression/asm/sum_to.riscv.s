# Hand-written RISC-V: the same loop as sum_to.x86-64.s, with no compiler directives.
	.text
sum_to:
	li	a1, 0               # s = 0
	li	a2, 1               # i = 1
.Lcond:
	bgt	a2, a0, .Ldone      # i > n ?
	add	a1, a1, a2          # s += i
	addi	a2, a2, 1           # i++
	j	.Lcond
.Ldone:
	mv	a0, a1
	ret
