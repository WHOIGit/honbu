## ADDED Requirements

### Requirement: Hydra composition with defined precedence
The framework SHALL compose configuration through the Hydra Compose API with a
defined search precedence (repeatable explicit directories, then a project-local
directory, then packaged defaults) and SHALL promote a named experiment selector
to a full configuration root.

#### Scenario: Local shadow
- **WHEN** a local group and a packaged group share a name
- **THEN** the local group is selected and omitted groups fall through to packaged

### Requirement: Mutually exclusive roots
The framework SHALL treat named-experiment selection, an authored root file, and
an exact resolved-config replay as mutually exclusive configuration roots.

#### Scenario: Conflicting roots
- **WHEN** more than one root source is supplied
- **THEN** composition fails naming the conflict

### Requirement: Dash-rule override grammar
Dash-free `key=value` tokens SHALL be composition overrides; dash-prefixed tokens
SHALL be command options, and unknown dash-prefixed tokens SHALL fail as command
errors.

#### Scenario: Mixed tokens
- **WHEN** a caller supplies a dash-free override and a dash-prefixed option
- **THEN** only the override reaches composition

### Requirement: Installed command surface
The framework SHALL provide a command harness a project registers its
subcommands with, threading each through the resolution and lifecycle flow and
exiting cleanly on a user-facing refusal.

#### Scenario: Refusal exit
- **WHEN** a command raises a user-facing refusal
- **THEN** the process prints the message and exits non-zero without a traceback
