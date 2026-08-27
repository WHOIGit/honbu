## ADDED Requirements

### Requirement: One sanitization rule for every platform
Values substituted into artifact paths SHALL be sanitized to the union of POSIX
and Windows component rules on every platform — forbidden characters, control
characters, reserved device stems, and trailing dots and spaces — so a
configuration authored on one platform produces the same path components on the
other.

#### Scenario: Same components everywhere
- **WHEN** a configuration value containing a platform-illegal character is
  substituted into an artifact path
- **THEN** the resulting component is identical on POSIX and Windows

#### Scenario: Reserved device stem
- **WHEN** a substituted value is a reserved device name
- **THEN** the emitted component is altered so the path is creatable

### Requirement: Sanitization is reported, never silent
When sanitization changes a substituted value, resolution SHALL report a warning
naming the original and the emitted component.

#### Scenario: Changed value
- **WHEN** a substituted value contains a character the union rule replaces
- **THEN** resolution warns naming both forms and proceeds

### Requirement: Absolute references are recognized in both syntaxes
The framework SHALL treat a schemed URI, a POSIX-absolute path, and a
Windows-absolute path as already-absolute, so a reference recorded on one
platform is not mistaken for a relative reference on the other.

#### Scenario: Cross-platform absolute path
- **WHEN** a drive-rooted path recorded on Windows is read on POSIX
- **THEN** it is treated as absolute rather than resolved against a parent

### Requirement: Sanitized strings never carry identity
No hash source SHALL derive from a sanitized path component. Path-derived
identity SHALL use separator-normalized values computed independently of
sanitization.

#### Scenario: Identity survives platform
- **WHEN** the same dataset is hashed on POSIX and on Windows
- **THEN** the digests are equal even where path components differ

### Requirement: Path length is refused before writing
On a platform enforcing a maximum path length, resolution SHALL refuse a run
directory that leaves insufficient budget for the artifacts written beneath it,
before any filesystem write, rather than truncating a path.

#### Scenario: Over budget
- **WHEN** a resolved run directory exceeds the platform path budget
- **THEN** resolution fails naming the path and its length, and nothing is written
