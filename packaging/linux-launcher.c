/* The executable carries a ZIP of the Python application after the ELF image. */
#define _XOPEN_SOURCE 700
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char *executable = realpath("/proc/self/exe", NULL);
    char **arguments = calloc((size_t)argc + 2, sizeof(*arguments));
    if (!executable || !arguments) {
        perror("Mouse Control");
        return 1;
    }
    arguments[0] = "/usr/bin/python3";
    arguments[1] = executable;
    for (int i = 1; i < argc; ++i) {
        arguments[i + 1] = argv[i];
    }
    execv(arguments[0], arguments);
    perror("Mouse Control: /usr/bin/python3");
    return 1;
}
