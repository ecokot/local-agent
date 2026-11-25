# Agent Skills

This directory contains reusable, project-independent instructions for AI agents working on software projects. The skills describe role behavior; they do not contain the current state, architecture, or stage plan of any particular project.

## Available skills

- [Project Planner](project-planner/SKILL.md) — investigates context, defines bounded tasks, coordinates implementation, independently reviews evidence, and handles acceptance.
- [Project Developer](project-developer/SKILL.md) — investigates existing code, implements assigned work, tests changes, and prepares a factual handoff report.

## Role separation

The Planner defines **what** must be achieved and decides whether the evidence satisfies the task. The Developer decides **how** to implement the agreed task and supplies the implementation and evidence. The Developer may challenge unclear or unsafe requirements; the Planner resolves scope and acceptance decisions. A Developer's self-assessment is not acceptance.

These files follow the open [Agent Skills format](https://agentskills.io/specification). A compatible agent host must discover and load the relevant skill for the instructions to affect runtime behavior. Merely storing a skill in this repository does not automatically activate it in every AI client.
