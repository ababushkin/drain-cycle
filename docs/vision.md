# Vision

## Problem

Doing a piece of work is never a single action. It's a sequence of steps - prompt an agent to write the code, review it against the right standards, open a PR, rewrite the PR description so a person can read it, and on from there. My agent handles any one of these steps well, but only one at a time, and only after I've set it up. Each step is its own conversation: I prompt it, refine what I mean, check what comes back.

This holds up fine for small work. But the more complex the work gets - the more thinking and planning it needs up front - the faster that workflow breaks down. And when it breaks, it's clear the thing holding the work together was me all along, doing three jobs I'd never thought to separate:

1. **Holding the state** - where the work stands: what's done, what's next, what's blocked. Ideally this lives in one organized place. By default, in agentic development, it scatters into loose markdown files that are painful to keep straight.
2. **Knowing the steps** - what the steps are, what order they run in, and the standard each one has to meet. That knowledge lives in my head and comes out a little differently every time.
3. **Supervising the run** - moving each piece of work from one step to the next, pushing a worker from "coding" to "reviewing" to "open the PR," kicking off each step, watching what comes back, and carrying on to the next.

The last two feel like a single job, because I do them in the same breath - I drive the work and carry the process in my head at the same time, and never notice they're two different things.

Carrying all three myself leaves me with two problems I can't get out from under:

1. **Execution is inconsistent.** Because the steps live in my head, the care they get depends on me. On a good day I break the work down and run every step methodically. On a worse one I get lazy, skip a step, or forget part of the process - so the same kind of work gets a different level of care depending on the day.
2. **I'm pinned to supervising.** Because the driving eats my attention, I can't do much else while my workers run. I can't step back to think about the next set of projects or to level myself up.

So the problem isn't doing the work. It's that holding it all together - the state, the steps, the supervision - falls entirely on me, and the more demanding the work, the worse that gets.

## Solution

The way out is to stop *being* the thing that holds the work together and build that thing once instead. It rests on three pieces, and they line up with the three things the problem dumps on me - the state, the steps, and the supervision.

**One place for state.** Everything starts from a single, organized record of where the work stands: what's done, what's next, what's blocked. Instead of each piece of work's status scattering across loose markdown files, there's one source of truth, and everything else works off it. This is the ground the rest stands on. Of the three, state was the one I already treated as its own job. The other two I carried fused - knowing the work and supervising it, done in the same breath. Pulled apart, each becomes something I can build.

**The steps become skills.** Knowing the work is knowing what the steps are, what order they run in, and what standard each one has to meet. That knowledge used to live in my head and come out a little differently every time. I capture it as skills: one skill per step, plus a sequence that runs them in order. The process is written down once, out in the open, the same on every run.

**The supervision becomes a supervisor.** The other half is the mechanical part - kick off each step, watch it, check it produced what it should, move to the next, stop and flag anything it can't get past. That's pure mechanics, the part of me that isn't thinking. It gets automated into a supervisor - I call it the drain-cycle - that drives the skills, verifies each result, updates the record, and carries each piece of work along on its own. I'm no longer the connector between steps. I built the connector, and my job moves up a level: I point it at a project and let it run.

**Before - I am the connector**

```
me     → "write the code for this"
agent  → writes it
me     → read it. "now review it against these standards"
agent  → reviews it
me     → "open a PR"
agent  → opens it
me     → "rewrite the PR description so it reads well"
agent  → rewrites it
me     → check it, mark it done
       → then start the whole chain over for the next piece of work
```

I'm there at every arrow - for every piece of work in the project. And on a tired day, I skip one.

**After - I built the connector**

```
me         → "work through this project"
supervisor → picks up the first piece of work
           → runs build → review → PR → finish, checking each result
           → marks it done, moves to the next
           → ...
           → hits one it can't get past review, stops, tells me why
me         → review the finished work; step in only where it halted
```

I'm there once at the start, and again only when something genuinely needs me.

**What changes for me**

Two things.

First, the work gets *more consistent than I am.* Because the steps are captured once and the supervisor runs them the same way every time, execution stops depending on my discipline. It doesn't get tired, skip a step, or forget the process on a bad day, so every piece of work gets the same level of care, not whatever level I happened to have that afternoon.

Second, my attention comes off the mechanical loop entirely. Instead of walking each piece of work through the same chain of steps, I move to the work that needs a person: scoping the next project, judging whether finished work is any good, and improving the system itself.

And this pays off hardest exactly where the problem hurt most. The more complex the work (the more steps, the more planning) the more the old way leaned on me and the more it broke down. The new way leans on the same captured process and the same supervisor no matter how big the project gets.
