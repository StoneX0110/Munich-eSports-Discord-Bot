# Test approach

Run `python -m pytest` from the repository root after installing
`requirements-test.txt`.

The workflow tests run real command callbacks, interactive views, scheduling
handlers and JSON persistence against temporary files. Discord messages and the
easyVerein transport are mocked; these are application-level integration tests,
not live-service end-to-end tests.

- Elections: create/delegate, open a ballot, submit concurrently, retry a failed
  disk write, reload the result, close the election and end the session.
- Weekly polls: create/post, toggle availability, flush/reload, remind non-voters,
  replace the weekly instance, reject old buttons and delete the configuration.
- Reminders: create/list, deliver on schedule, suppress duplicates, force a send
  and delete; failures leave the reminder retryable.
- Membership/moderation: API date serialization, membership eligibility, daily
  role synchronization and evidence/ban/report ordering.

Input variations share parameterized tests. Helpers are covered through these
workflows instead of separate forwarding/formatting/constant tests. Focused
concurrency tests remain for network interleavings, dialog cleanup and coalescing
that an ordinary sequential workflow cannot exercise.
