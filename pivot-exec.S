.section .text
.global _start

_start:
    mov 8(%esp), %ebx          /* argv[1]: new root */
    mov $61, %eax              /* __NR_chroot */
    int $0x80
    test %eax, %eax
    js fail

    sub $8, %esp
    mov $init_path, (%esp)      /* argv[0] */
    mov $0, 4(%esp)             /* argv[1] = NULL */
    mov %esp, %ecx              /* argv */
    xor %edx, %edx             /* envp = NULL */
    mov $init_path, %ebx
    mov $11, %eax               /* __NR_execve */
    int $0x80

fail:
    mov $1, %eax                /* __NR_exit */
    mov $1, %ebx
    int $0x80

.section .rodata
init_path:
    .asciz "/sbin/init"
