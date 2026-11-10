# Job Search Harness release copy

## One line

Job Search Harness is a local workspace for screening job listings, preparing applications, and tracking recruiter follow-ups.

## Short post

Job Search Harness keeps job descriptions, match reasons, application drafts, and recruiting history in one local workspace. Review a résumé and recipient before sending through NetEase 126, or record a submission you made in a recruiting platform. The offline demo uses fictional data and simulated sending.

[Repository and setup](https://github.com/milkrabbitt/job-search-harness)

## Longer post

Job Search Harness keeps the job description alongside the application and any recruiter conversation you have recorded or synced. It runs locally and has a Chinese web console.

Start by adding your verified personal facts, an approved résumé PDF, and search preferences. Harness can read configured public company sources, search Liepin with candidate authorization, or work with a job description you add yourself. Its matching rules show what fits, where the role conflicts with your preferences, and what details are missing. An optional local Codex CLI can add a review. You can see any recorded salary range in the jobs list and export the ledger to CSV for your own comparisons.

With NetEase 126 configured, Harness can send approved applications and handle a narrow set of preauthorized replies in trusted email threads. You complete official forms and platform chats in your browser, then record the result. Interview times, salary decisions, and offers stay with you.

The demo uses fictional jobs, applications, and conversations. Run `./scripts/bootstrap.sh --demo` followed by `./scripts/serve.sh`. The README has the system requirements and a prompt you can give to a coding assistant to set it up. [Repository and setup](https://github.com/milkrabbitt/job-search-harness)
