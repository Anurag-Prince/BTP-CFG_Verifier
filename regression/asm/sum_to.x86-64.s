// Hand-written x86-64 (AT&T syntax): int sum_to(int n) { int s=0; for (i=1;i<=n;i++) s+=i; return s; }
// No .type/.globl/.cfi directives on purpose: the reader must cope with assembly that no compiler wrote.
	.text
sum_to:
	movl	$0, %eax            # s = 0
	movl	$1, %ecx            # i = 1
.Lcond:
	cmpl	%edi, %ecx          # i <= n ?
	jg	.Ldone
	addl	%ecx, %eax          # s += i
	addl	$1, %ecx            # i++
	jmp	.Lcond
.Ldone:
	ret
