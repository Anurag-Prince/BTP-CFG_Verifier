// Hand-written AArch64: the same loop as sum_to.x86-64.s, with no compiler directives.
	.text
sum_to:
	mov	w1, #0              // s = 0
	mov	w2, #1              // i = 1
.Lcond:
	cmp	w2, w0              // i <= n ?
	b.gt	.Ldone
	add	w1, w1, w2          // s += i
	add	w2, w2, #1          // i++
	b	.Lcond
.Ldone:
	mov	w0, w1
	ret
