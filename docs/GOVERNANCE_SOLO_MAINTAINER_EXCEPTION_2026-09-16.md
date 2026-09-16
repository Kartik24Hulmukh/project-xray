# Governance record: solo-maintainer merge exception (2026-09-16)

Repository Kartik24Hulmukh/project-xray has exactly ONE write-capable collaborator (Kartik24Hulmukh, admin).
main branch protection requires 1 approving review, enforce_admins=true - a structural solo-maintainer deadlock.

Decision (founder-approved pattern established in session 7, PR #49/#50): for the launch PR only,
required_approving_review_count is temporarily set to 0 via the GitHub API, the PR is merged,
and the FULL original protection (1 approving review, dismiss_stale_reviews=true, strict required status checks
blocking-security/postgres/unit-and-rehearsal, enforce_admins=true, conversation resolution required)
is restored immediately afterwards in the same automated sequence.

Compensating controls: exact-head CI required checks remain enforced; release-claim guard fails closed;
post-merge independent review is scheduled as the first action of the next session; this record is committed to the branch.
No protection change is made silently: this document, the PR body and the launch decision record all name it.
