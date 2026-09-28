# State the intent

Since 0.30, every commit an agent makes in a session can say what the task was for, in the person's words, before any code existed:

```
Gitvow-Intent: Let ops export orders as CSV from the admin page. by nikhil source=prompt
```

The only record of what a change was meant to achieve used to be a commit message written after the fact, by the agent. The card, the reviewer, the digest and the outcome join all judged the change without it. The intent is one line, kept in the session state inside `.git` (never in the tree), written by the `prepare-commit-msg` hook on each of the agent's commits in the session, and copied into the [session note](../reference/note.md).

## Where it comes from

Two sources, and the trailer says which:

- **The first message of the session, in Claude Code.** gitvow's `UserPromptSubmit` hook takes the first line of the person's first message, runs redaction over it, keeps at most 200 characters, and records it with `source=prompt`. Only the first message; later messages never replace it. A slash command, a one- or two-word nudge ("go", "yes please") or a message that is only pasted content is not an intent and records nothing. The message itself is never stored anywhere; the hook log counts the event without the words.
- **A person states it.** `gitvow intent "<what this task is for>"` records it with `source` absent, meaning stated. A stated intent outranks a captured one, so restating it mid-session, when the task changes, replaces whatever the first message said.

```sh
gitvow intent "tighten the authz rules before the staging cut"   # state it
gitvow intent                                                    # print what is recorded
gitvow intent --clear                                            # drop it
gitvow intent --by priya "ship the export"                       # someone else's words, recorded as theirs
```

Identity comes from `git config user.email` (its local part) or `user.name`, as for decisions.

At session start the agent is told how this works: that the first message becomes the intent, and that if the message does not say what the task is for it should ask once, in one line, before the first edit, and record the answer verbatim with `gitvow intent`. It is told never to invent one. Both behaviours are policy:

```json
"intent": {"from_prompt": true, "ask_when_missing": true}
```

`from_prompt: false` records nothing until a person runs `gitvow intent`. `ask_when_missing: false` says nothing to the agent.

Other agents have no message hook that gitvow reads yet, so with Codex, Cursor, Gemini, Copilot and Factory the intent comes from `gitvow intent`, run by the person or by the agent after asking. The trailer, the note and everything below are the same.

## Where it goes

- **Every commit of the session**, as the trailer above. Agent commits only: a person's commit from a terminal during the session carries no session and no intent. When a session reaches a second repository (`git -C`, or an edit in another checkout), the intent follows it, so the commit there carries the same line.
- **The session note** (`intent`: text, by, source, when, the session and step it was set at) and the ledger.
- **The card.** When an intent is recorded, the card opens with it, and each open finding that shares a word with it is marked `[within the stated intent]`.
- **`gitvow report`** and the pull request comment: an **Intent** line above the plan on each commit.
- **`gitvow handoff`**, `gitvow recall` (the intent is searched like the plan), and the record server's `record_intent` tool: the current session's intent and what recent commits said they were for.
- **`gitvow digest`**: one line, below.

## Within the intent

"Within the stated intent" is a word overlap between the intent and the finding's subject: the path split on punctuation and camel case, the route, the command. Words that name the shape of a finding rather than its subject (`edit`, `route`, `push`, `production`, file extensions) are ignored, so an intent cannot cover everything by mentioning "edit the file". Nothing cleverer, and it never answers a finding. It is context next to the question, recorded on the finding and in the note as `intent_covered`, so the digest can count it:

```
Intent: 4 of 6 sessions stated one · 5 of 9 card findings fell within it (4 accepted, 1 declined)
```

That line is the experiment this utility exists for: whether asking once at task start removes more consequential questions than gating each action does. If, over a period, most findings within a stated intent are accepted and most declines fall outside it, the card can one day propose the answer from the intent, or stop asking about findings the intent already names. Until the record says so, it asks. No figure in it is about a person.

## What it is not

- Not a plan. The plan is the agent's last words before committing (`last_stated_plan`, "said versus did"); the intent is the person's words before the agent started.
- Not a claim. A claim is a statement about the code confirmed as true; an intent is a purpose, and may turn out to be unmet.
- Not a permission. No intent, however worded, changes what the gate allows, asks or denies.
