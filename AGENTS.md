# Agent guidelines
Instructions for coding agents (Claude Code, Codex, Antigravity, Cursor agents) working in this repository.

## Review process
Every pull request is reviewed by a different model from the one that implemented it.

| implementer | reviewer |
|---|---|
| Claude | Codex |
| Codex | Claude |
| Antigravity | Claude |
| Cursor cloud agent | Codex |

1. **Draft first.** Open the PR as a draft while iterating; only the Linux CI jobs run on drafts. Mark it ready for
   review when the work is done; that triggers the macOS jobs.
2. **Review.** The reviewer posts its findings on the PR with `gh pr review --comment` and inline comments
   (`gh api repos/{owner}/{repo}/pulls/{n}/comments`). Start every review body and comment with
   `Review by <model name>` (for example `Review by Claude Opus 5.5`). Look for, in order:
   - correctness and bugs;
   - realtime safety: no allocation, locks, blocking calls or syscalls on the audio thread;
   - numerical and DSP validity, checked against the references (ngspice, the ground-truth renders, the Python
     model), and pass limits that were not loosened to fit a result;
   - tests: does each change have one, and does it test behaviour rather than implementation;
   - maintainability.

   Say which comments block the merge and which are suggestions.
3. **Address.** The implementer resumes its previous session for the PR, so it keeps its context, and answers every
   comment on the PR: what it changed (with the commit), or why it did not. Rerun the affected tests and push.
4. **Push back when it is right to.** Pushback is allowed and encouraged when it leads to code that is more
   maintainable, correct and bug-free. Reply with the reasoning and evidence instead of complying blindly. Reviewers
   should likewise accept good pushback, and say so on the PR. The reviewer does one short re-review pass to confirm
   each comment is resolved or the pushback is accepted.
5. **Refactor when it helps.** Refactoring is encouraged when it makes the code more extensible and future work
   easier. Keep it within the PR's scope as a clearly separate commit, or open a follow-up issue if it is larger.
6. **Never merge without Michael's approval.**
