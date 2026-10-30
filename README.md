# Dev Tools

---
By Pradhya for [Project Enclave](https://github.com/project-enclave) but mainly for [Enclave Messenger](https://github.com/project-enclave/Enclave-Messenger)
> [!NOTE]
> this is not a part of [Project Enclave](https://github.com/project-enclave)
> and if youre wondering how i know i can do stuff like `!NOTE`, well... i didnt... im using lazyvim so thats how i know about this. bleh

## What does this do?

Start a Python project with a `src` package, tests, configuration, a `main` Git
branch, and a GitHub Actions workflow:

```bash
tools init ./my-project --name my-project
```

When `gh` is installed and authenticated, init creates a private GitHub
repository and adds it as `origin`. Use `--visibility public` to create a public
repository, or `--no-github` to skip GitHub setup. Init does not stage, commit,
or push project files. The workflow runs lint, type checks, security scans, and
tests on Linux, macOS, and Windows, builds distributions, and creates a GitHub
release when you push a `v*` tag.
