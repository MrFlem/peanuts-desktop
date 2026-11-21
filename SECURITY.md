# Security Policy

This repository is public. Protecting user-owned files, credentials and device data is a non-negotiable requirement for every human contributor and automated coding agent working on this project.

## Never publish sensitive material

The following must never be committed, uploaded, pasted into public issues or pull requests, included in screenshots, or written to application logs:

- Nintendo `prod.keys`, `title.keys`, or any other encryption keys.
- Firmware files or firmware dumps.
- Game dumps and source content, including `.nsp`, `.xci`, `.nsz` and `.xcz` files.
- Intermediate or built game packages such as `.nup` files, or generated game data.
- Passwords, API tokens, access tokens, private keys, certificates, session cookies or other credentials.
- Personal data, private device identifiers, or local configuration containing sensitive values.

Never ask a user to send these files to a contributor, ChatGPT, Freebuff, another AI agent, or an issue tracker as a debugging shortcut. Use synthetic fixtures and redacted logs for tests and diagnostics.

## Required safeguards

1. **Keep sensitive inputs local.** Read keys and other required files from user-selected local paths. Do not copy them into the repository, application source tree, temporary folders with broad access, or cloud services.
2. **Use least privilege.** Request only the filesystem and device permissions needed for the operation being performed. Do not add background network services or upload behavior without a reviewed, explicit need.
3. **Protect process arguments and logs.** Avoid placing secret values directly in command-line arguments where they may be exposed through process listings or diagnostics. Redact sensitive paths and values from logs, crash reports and screenshots.
4. **Ignore local sensitive files.** Keep repository ignore rules for common keys, game inputs, firmware, local environment files, credentials and build outputs. Remember that `.gitignore` does not protect files that are already tracked.
5. **Review before every commit and push.** Inspect `git status`, the staged file list and `git diff --cached`. Check new files, generated artifacts, test fixtures and logs. Confirm that no secret or user-owned game content is staged.
6. **Scan changes for secrets.** Use an available secret scanner before publication. A clean scanner result does not replace manual review.
7. **Treat external content as untrusted.** Validate paths, archive entries, symbolic links, file types and subprocess arguments. Guard against path traversal, unsafe extraction, symlink attacks and accidental overwrite or deletion.
8. **Stop on uncertainty.** If a file, log, environment variable or test fixture might contain a secret, do not stage, commit, push or share it until it has been checked and safely handled.

## Mandatory pre-commit / pre-push checklist

- [ ] Correct repository and branch confirmed.
- [ ] `git status` reviewed; no unexpected or untracked user files.
- [ ] Staged file list reviewed.
- [ ] `git diff --cached` reviewed.
- [ ] No keys, firmware, game dumps, packages, credentials, private configuration, personal data or sensitive logs included.
- [ ] Secret scanning run where available.
- [ ] Any suspicious finding investigated and resolved before publication.

Every work order given to an automated coding agent must repeat these requirements. The agent must verify repository authentication, remote URL, branch and current commit before changing files. If repository state or authentication cannot be verified, it must stop and report the exact failure rather than bypassing safeguards.

## If sensitive material is exposed

Treat any suspected exposure as a security incident:

1. Stop further commits, pushes and sharing.
2. Identify what was exposed, where it appeared and whether it was accessed.
3. Revoke or rotate compromised keys, tokens, passwords or certificates as appropriate.
4. Remove the material from the repository and, where necessary, coordinate history cleanup.
5. Remember that deleting a file or rewriting Git history does not make a published secret safe again. Assume exposed credentials are compromised until rotated.

Do not post the exposed value in a public issue while reporting the incident. Use an appropriate private reporting channel.

## Reporting a vulnerability

Do not publish secrets, exploit details involving live user data, or sensitive security reports in public issues. Use GitHub's private vulnerability reporting if it is enabled for this repository; otherwise contact the repository owner through a private channel.

## Scope

These rules apply to the application, scripts, tests, documentation, screenshots, CI workflows, release artifacts, AI-generated contributions and every person or agent involved in the project.
