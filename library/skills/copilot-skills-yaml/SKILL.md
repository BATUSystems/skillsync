---
name: copilot-skills-yaml
description: Guide for creating and editing YAML front matter in GitHub Copilot skill files with correct syntax and supported attributes
user-invokable: true
---

# Copilot Skills YAML Guide

This skill provides knowledge about creating and editing YAML front matter for GitHub Copilot skill files. Covers official GitHub Copilot specification, Agent Skills open standard, best practices from awesome-copilot, and Jekyll/YAML conventions.

## Agent Skills Open Standard

Agent Skills is an **open format** maintained by Anthropic and supported by:
- GitHub Copilot (all versions)
- Copilot CLI
- Multiple AI systems and platforms

**Resources**:
- Specification: https://agentskills.io/specification
- Repository: https://github.com/agentskills/agentskills
- Community Examples: https://github.com/anthropics/skills
- Awesome Collections: https://github.com/github/awesome-copilot

See [Creating Agent Skills](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-skills) in GitHub Copilot documentation.

## Storage Locations

### Project Skills
Repository-specific skills stored in:
- `.github/skills/{skill-name}/SKILL.md` (GitHub Copilot, GitHub recommended)
- `.claude/skills/{skill-name}/SKILL.md` (Claude, alternative)

**Scope**: Available only within this repository

### Personal Skills
Shared across projects:
- `~/.copilot/skills/{skill-name}/SKILL.md` (GitHub Copilot)
- `~/.claude/skills/{skill-name}/SKILL.md` (Claude)

**Scope**: Available across all repositories and projects

### Organizational & Enterprise (Coming Soon)
- Organization-level skills (shared across org repositories)
- Enterprise-level skills (shared across enterprise)

## Supported YAML Attributes

### Required Attributes
- **name** - Skill identifier (kebab-case, unique)
- **description** - Brief description of the skill (single-line text)

### Optional Attributes
- **user-invokable** - Allow users to invoke skill directly (`true` or `false`)
- **argument-hint** - Hint for skill arguments
- **compatibility** - Compatibility information
- **disable-model-invocation** - Disable automatic model invocation
- **license** - License information
- **metadata** - Additional metadata

### NOT Supported Attributes
The following attributes will cause validation errors:
- `keywords` - Not supported
- `skill` - Do not nest attributes under this key
- Custom fields or arbitrary keys

## Correct YAML Syntax

### Valid Example
```yaml
---
name: example-skill
description: Brief single-line description of what this skill does
user-invokable: true
---
```

### Common Mistakes to Avoid

#### 1. Multi-line Description with Pipe
❌ **INCORRECT** (causes "Unexpected indentation" errors):
```yaml
description: |
  This is a multi-line description.
  It will cause validation errors.
```

✅ **CORRECT** (single-line):
```yaml
description: Brief single-line description of what this skill does
```

#### 2. Nesting Under 'skill' Key
❌ **INCORRECT**:
```yaml
skill:
  name: example-skill
  description: Example
```

✅ **CORRECT** (attributes at root level):
```yaml
name: example-skill
description: Example
```

#### 3. Using Keywords Field
❌ **INCORRECT**:
```yaml
keywords:
  - keyword1
  - keyword2
```

✅ **CORRECT** (omit keywords entirely):
```yaml
description: Complete description including relevant terms
```

#### 4. Extra Information in Description
❌ **INCORRECT**:
```yaml
description: |
  Main description here.
  
  Use this skill when: working with X, Y, Z.
  
  Keywords: foo, bar, baz
```

✅ **CORRECT** (concise single-line):
```yaml
description: Main description covering the skill's purpose
```

## Markdown Content Rules

### Table of Contents
**Avoid** Table of Contents with anchor links - they cause "file not found" errors:

❌ **INCORRECT** (anchor links cause validation errors):
```
## Table of Contents
1. Section One (with anchor link)
2. Section Two (with anchor link)
```

✅ **CORRECT** (omit ToC or use plain text):
```markdown
# Skill Title

## Section One
Content...

## Section Two
Content...
```

### Internal Links
**Avoid** anchor links in markdown content (links starting with # symbol pointing to sections).

❌ **INCORRECT**: Using bracketed links with hash anchors will fail validation

✅ **CORRECT** (use plain reference):
```markdown
See "My Section" section below for details.
```

## Complete Template

```yaml
---
name: skill-name
description: Brief description of the skill's purpose and functionality
user-invokable: true
---

# Skill Title

Brief overview of what this skill provides.

## Section One

Content without anchor links or ToC.

## Section Two

More content following the same pattern.

## Key Concepts

- Point 1
- Point 2
- Point 3

## Code Examples

```bash
# Example commands
command --flag value
```

## Best Practices

1. Keep description concise
2. Organize content with clear sections
3. Use code blocks for examples
4. Reference files with relative paths

## Related Skills

- See **other-skill** for related information
- Check **another-skill** for complementary knowledge

---

**Maintainer:** [Your name]
```

## Validation Workflow

### 1. Check for Errors
After creating/editing a skill file:
```bash
# In VS Code, check Problems panel
# Or use get_errors tool to verify
```

### 2. Common Error Messages

**"Unexpected indentation"**
- Cause: Multi-line YAML value with pipe `|`
- Fix: Convert to single-line description

**"Attribute 'X' is not supported"**
- Cause: Using unsupported field or improper nesting
- Fix: Remove the field or use supported attributes

**"Nie znaleziono pliku" (File not found)**
- Cause: Anchor links in markdown (e.g., `#section-name`)
- Fix: Remove Table of Contents or replace with plain text

### 3. Verification Checklist
- [ ] Front matter uses only supported attributes
- [ ] Description is single-line (no pipe `|`)
- [ ] No Table of Contents with anchor links
- [ ] No internal anchor links in content
- [ ] No `keywords:` field
- [ ] Attributes at root level (not nested under `skill:`)

## Accompanying Resources

Skills can include supplementary files that Copilot can use:

```
.github/skills/skill-name/
├── SKILL.md              # Main skill file (required)
├── script.sh             # Auxiliary shell script
├── example.py            # Example implementation
├── REFERENCE.md          # Extended reference guide
└── templates/            # Template files
    ├── template1.txt
    └── template2.txt
```

In `SKILL.md` instructions, reference these resources:
- **Scripts**: Tell Copilot when and how to use them
- **Examples**: Show practical usage patterns
- **Templates**: Guide for generating similar files
- **References**: Detailed documentation

**Important**: Copilot follows your instructions to decide if/when to use these resources.

## How Copilot Uses Skills

### Skill Discovery
When you give Copilot a task, it:
1. Analyzes your prompt and context
2. Matches it against available skill descriptions
3. Decides which skills are relevant

### Skill Activation
When Copilot chooses a skill:
1. Entire `SKILL.md` content is injected into Copilot's context
2. Your `description` field determines relevance matching
3. Copilot accesses bundled resources (scripts, examples, etc.)
4. Instructions are followed to complete the task

### Description's Critical Role
**Your description field is the filter** for skill relevance:
- Clear, specific descriptions → Higher accuracy matching
- Vague descriptions → Copilot may miss opportunities to use the skill
- Long descriptions → Wastes context; keep concise

**Example**:
```yaml
# ❌ POOR: Too vague
description: Guide for development practices

# ✅ EXCELLENT: Specific + when-to-use
description: Guide for Angular Material table server-side sorting, filtering, and pagination patterns with column-level control
```

Include the technologies/domains your skill covers in the description.

## Skills vs Custom Instructions

### Use Custom Instructions for:
- Simple guidelines applicable to almost every task
- Repository-wide coding standards
- Team conventions that apply broadly
- Information that should be always available

**Location**: `.github/copilot-instructions.md` (root level)

### Use Skills for:
- Detailed, specialized instructions for specific tasks
- Domain-specific procedures (e.g., shipping support, rating reporting)
- Self-contained modules with examples, scripts, or templates
- Focused guidance that Copilot should access when relevant

**Location**: `.github/skills/{skill-name}/SKILL.md`

**Decision**: Skills are loaded contextually; custom instructions are always loaded. Use skills for focused expertise.

## Jekyll/YAML Best Practices

### Optional Variables
**YAML front matter variables are optional** - define them as needed:

```yaml
---
name: skill-name
description: Skill description
user-invokable: true
custom-metadata: value          # Custom variables supported
shift-version: 1.0              # Your own fields can be added
---
```

### DRY Principle (Don't Repeat Yourself)
For repositories with multiple skills:

```yaml
# DEFAULT values (base template) in documentation
user-invokable: true
license: MIT

# OVERRIDE in specific skills if different
user-invokable: false           # Only override when needed
```

In future: Define defaults once, override per-skill where necessary.

### UTF-8 Encoding Requirement

⚠️ **IMPORTANT**: All skill files must be UTF-8 encoded without BOM (Byte Order Mark)

**Why**: BOM header characters (```\xEF\xBB\xBF```) cause YAML parsing errors

**Check your editor**:
- VS Code: Bottom right corner should show "UTF-8" not "UTF-8 with BOM"
- Set in `.editorconfig`: `charset = utf-8` (no BOM)

### Frontmatter Variables in Markdown
YAML variables are accessible in Markdown using Liquid tags (Jekyll/Claude systems):

```markdown
---
name: example-skill
ver-sion: 2.1.0
author: Your Name
---

# {{ page.name }}

Author: {{ page.author }}
Version: {{ page.version }}
```

**Supported Scopes**:
- **Global**: Predefined by system (layout, permalink, published)
- **Post/Custom**: Your defined frontmatter variables
- **Available in**: Markdown content, included files, layouts

## Best Practices

### Description Writing
- **Be specific** - Clearly state what the skill covers AND when to use it
- **Be concise** - One sentence, max two lines
- **Include key terms** - Mention main technologies/concepts/domains
- **Avoid meta-text** - Don't say "Use this skill when..." (shown automatically)
- **Enable discovery** - Help Copilot match your skill to user intent

### Content Organization
- **Clear sections** - Use H2 headings for main topics
- **Code examples** - Include practical examples
- **Reference other skills** - Link by name, not anchor
- **Keep it focused** - One skill, one domain

### File Naming Conventions

**Directory Name** (from awesome-copilot guidelines):
- **Format**: `kebab-case` (lowercase with hyphens)
- **Length**: Descriptive but concise
- **Hyphens for spaces**: `data-visualization` not `data_visualization`
- **No underscores**: Use hyphens consistently

✅ **Examples**:
```
.github/skills/shop-rating-reporting/SKILL.md
.github/skills/backend-architecture/SKILL.md
.github/skills/shop-shipping-support/SKILL.md
.github/skills/testing-guidelines/SKILL.md
```

❌ **Avoid**:
```
.github/skills/rating_reporting/          # Use hyphens, not underscores
.github/skills/RatingReporting/           # Must be lowercase
.github/skills/shop-rating-reporting/Skill.md  # File must be SKILL.md (uppercase)
```

**File Name**: Always `SKILL.md` (uppercase, exact spelling)

**Complete Path Pattern**:
```
.github/skills/{kebab-case-name}/SKILL.md
```

## Example: Converting Invalid to Valid

### Invalid Skill File
```yaml
---
skill:
  name: example
  description: |
    Multi-line description with extra info.
    
    Use this when working with examples.
  keywords:
    - example
    - demo
---

# Example Skill

## Table of Contents
1. Section (with anchor link to #section)

## Section

See Table of Contents (with anchor link).
```

### Valid Skill File
```yaml
---
name: example
description: Example skill demonstrating correct YAML syntax and markdown structure
user-invokable: true
---

# Example Skill

This skill demonstrates correct formatting.

## Section

See "Table of Contents" section above for overview.
```

## Troubleshooting

### YAML Parser Errors
1. Check indentation (2 spaces, not tabs)
2. Ensure no trailing spaces after values
3. Verify quotes are balanced if used
4. Check for special characters that need escaping

### Markdown Linter Errors
1. Remove all anchor links (`#section-name`)
2. Remove Table of Contents
3. Use plain section references instead of links
4. Keep relative file paths valid

### Validation Fails After Edit
1. Re-read the error message carefully
2. Check line numbers in error output
3. Compare with working examples
4. Verify all attributes are supported

## Integration with copilot-instructions.md

Skills are referenced in main instructions file:

```markdown
<skills>
<skill>
<name>skill-name</name>
<description>Brief description</description>
<file>/path/to/.github/skills/skill-name/SKILL.md</file>
</skill>
</skills>
```

**Note**: The `<description>` in copilot-instructions.md and `description:` in YAML should match or complement each other.

### Integration with Plugins

Skills bundled in plugins (from awesome-copilot):

```toml
# plugin.toml or plugin configuration
name = "web-development"
version = "1.0.0"
skills = [
  "angular-patterns",
  "typescript-guidelines",
  "testing-best-practices"
]
```

Users install via:
```bash
copilot plugin install web-development@awesome-copilot
```

Skills can exist independently or bundled with others.

## External Resources & Links

For comprehensive, up-to-date information:

1. **Official Documentation**
   - [Creating Agent Skills](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-skills) - GitHub official guide
   - [About Agent Skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills) - Concept overview
   - [Repository Custom Instructions](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions) - Custom instructions guide

2. **Tech Specifications**
   - [Agent Skills Specification](https://agentskills.io/specification) - Official open standard
   - [Awesome Agent Skills](https://github.com/agentskills/agentskills) - Specification & SDK
   - [Anthropic Skills Examples](https://github.com/anthropics/skills) - Real-world examples

3. **Community Collections**
   - [GitHub Awesome Copilot](https://github.com/github/awesome-copilot) - 250+ contributed skills, agents, instructions
   - [Skills Documentation](https://github.com/github/awesome-copilot/blob/main/docs/README.skills.md) - Skills guide
   - [Contributing Guidelines](https://github.com/github/awesome-copilot/blob/main/CONTRIBUTING.md) - Community standards

4. **Markdown & YAML**
   - [Jekyll Front Matter](https://jekyllrb.com/docs/front-matter/) - Variables, scopes, DRY principle
   - [YAML Syntax](https://yaml.org/spec/1.2/spec.html) - Official YAML specification
   - [Liquid Template Language](https://shopify.github.io/liquid/) - Variable templating

## Troubleshooting Checklists

### Pre-Creation Checklist
- [ ] Skill directory name is `kebab-case` (lowercase, hyphens only)
- [ ] File will be named `SKILL.md` (uppercase, exact spelling)
- [ ] Full path: `.github/skills/{name}/SKILL.md`
- [ ] UTF-8 encoding without BOM in editor
- [ ] Description is single-line and specific (2-3 sentences max)

### Post-Creation Checklist
- [ ] YAML front matter uses only supported attributes
- [ ] Description includes key technologies/domains
- [ ] No `keywords:` field present
- [ ] No multi-line YAML values with pipe (`|`)
- [ ] No Table of Contents with anchor links
- [ ] No internal anchor links
- [ ] Attributes at root level (not under `skill:`)
- [ ] Run `get_errors` or check VS Code Problems panel
- [ ] All supporting files referenced in SKILL.md

### Deployment Validation
- [ ] Skill appears in `.github/copilot-instructions.md` Skills Reference
- [ ] Copilot can load and use the skill
- [ ] Description triggers skill on relevant prompts
- [ ] Supporting resources accessible from instructions

---

**Summary**: Keep YAML simple (single-line values, supported attributes only), avoid ToC and anchor links in markdown, and always validate after changes. Use open standard locations (`.github/skills` preferred), follow kebab-case naming, ensure UTF-8 encoding, and write clear descriptons to help Copilot discover your skill.
