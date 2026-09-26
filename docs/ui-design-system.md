# UI Design System

## Overview

This document defines UI patterns for WCComps. The system builds on Django Admin CSS with Cotton components for reusable UI elements.

---

## Design Principles

1. **Consistency** - Same patterns across all pages
2. **Role-Based** - UI adapts to user permissions
3. **Minimal** - Only show what's needed
4. **Accessible** - Semantic HTML, proper labels, keyboard navigation

---

## Color Palette

Colors are defined as CSS variables in `base.html` with fallback values. Use variables for dark mode compatibility.

### Semantic Colors

| Name | Variable | Fallback | Usage |
|------|----------|----------|-------|
| **Primary** | `--primary` | `#417690` | Links, headers, primary actions, info states |
| **Success** | `--success-fg` | `#28a745` | Success messages, positive scores, approved states |
| **Warning** | `--warning-fg` | `#f57c00` | Warnings, pending states, claimed tickets |
| **Danger** | `--error-fg` | `#ba2121` | Errors, deletions, negative scores, rejected states |
| **Info** | `--info-fg` | `#1976d2` | Open tickets, informational badges |

### Background Colors

Use utility classes from `base.html` for consistent backgrounds:

| State | Class | Background | Border | Text |
|-------|-------|------------|--------|------|
| **Success** | `.bg-success-light` | `#e8f5e9` | `#388e3c` | `#388e3c` |
| **Warning** | `.bg-warning-light` | `#fff3cd` | `#ffc107` | `#856404` |
| **Danger** | `.bg-danger-light` | `#ffebee` | `#ba2121` | `#ba2121` |
| **Info** | `.bg-info-light` | `#e8f4f8` | `#417690` | `#417690` |
| **Neutral** | `.bg-light` | `var(--darkened-bg)` | `#ddd` | `#666` |

### Score Colors

Use `.score-*` classes for automatic coloring:

| Context | Class | Color | Usage |
|---------|-------|-------|-------|
| **Positive** | `.score-positive` | `var(--success-fg)` | Point gains, bonuses |
| **Negative** | `.score-negative` | `var(--error-fg)` | Point deductions, penalties |
| **Neutral** | `.score-zero` | `var(--body-quiet-color)` | Base scores, unchanged values |

---

## Typography

### Hierarchy

| Element | Size | Weight | Color | Usage |
|---------|------|--------|-------|-------|
| Page Title | 24px | 400 | `var(--body-fg)` | Via `<c-page_header>` |
| Section Header | 18px | 600 | `var(--body-fg)` | `<h2>` in modules |
| Subsection | 16px | 600 | `var(--body-fg)` | `<h3>` in fieldsets |
| Body | 14px | 400 | `var(--body-fg)` | Default text |
| Small/Help | 12px | 400 | `var(--body-quiet-color)` | Help text, timestamps |
| Monospace | 13px | 400 | `var(--body-fg)` | Code, IPs, technical data |

### Text Utility Classes

| Class | Size | Color | Usage |
|-------|------|-------|-------|
| `.text-xs` | 11px | — | Extra small |
| `.text-sm` | 12px | — | Small text |
| `.text-md` | 14px | — | Medium (default) |
| `.text-lg` | 16px | — | Large |
| `.text-xl` | 18px | — | Extra large |
| `.text-muted` | — | `var(--body-quiet-color)` | Secondary text |
| `.text-primary` | — | `var(--primary)` | Primary color |
| `.text-success` | — | `var(--success-fg)` | Success color |
| `.text-danger` | — | `var(--error-fg)` | Danger color |

### Font Stack

Django Admin default fonts are used. No custom font stack is defined.

---

## Spacing Scale

| Name | Value | Usage |
|------|-------|-------|
| `xs` | 5px | Tight spacing, badge padding |
| `sm` | 10px | Form field gaps, list item padding |
| `md` | 15px | Section gaps, card padding |
| `lg` | 20px | Module padding, major sections |
| `xl` | 30px | Page sections, large gaps |

### Spacing Utility Classes

Margin and padding utilities defined in `base.html`:

| Margin | Padding | Value |
|--------|---------|-------|
| `.m-0` | `.p-0` | 0 |
| `.mt-5`, `.mb-5` | `.p-5` | 5px |
| `.mt-10`, `.mb-10`, `.ml-10` | `.p-10` | 10px |
| `.mt-15`, `.mb-15` | `.p-15` | 15px |
| `.mt-20`, `.mb-20`, `.my-20` | `.p-20` | 20px |

Layout utilities: `.d-flex`, `.d-none`, `.d-block`, `.flex-1`, `.items-center`, `.items-start`, `.items-end`, `.justify-between`, `.gap-5`, `.gap-10`, `.gap-15`, `.gap-20`

---

## Layout Patterns

### Page Structure

```
┌─────────────────────────────────────────────────────────┐
│ Header (branding, user menu)                            │
├─────────────────────────────────────────────────────────┤
│ Primary Navigation (role-based links)                   │
├─────────────────────────────────────────────────────────┤
│ Sub-Navigation (context-specific, e.g., scoring tabs)   │
├─────────────────────────────────────────────────────────┤
│ Page Header (title + optional subtitle)                 │
├─────────────────────────────────────────────────────────┤
│ Content Module(s)                                       │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Module Toolbar (back link, actions)                 │ │
│ ├─────────────────────────────────────────────────────┤ │
│ │ Module Content (forms, tables, details)             │ │
│ └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Grid Layouts

| Pattern | Columns | Usage |
|---------|---------|-------|
| Stats Grid | 3 equal | Dashboard metrics |
| Detail Grid | 2 equal | Two-column detail views |
| Form Grid | 1 column | Form fields (stacked) |
| Table | Full width | Data lists |

---

## Components

### Existing Cotton Components

#### `<c-page_header>`

Page title with optional subtitle.

```django
<c-page_header title="Review Incident Reports" subtitle="Match incident reports to red findings" />
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `title` | string | required | Main heading (h1) |
| `subtitle` | string | - | Secondary text below title |

---

#### `<c-module>`

Content container with optional toolbar and filtering.

```django
<c-module class="custom-class" filtered="true">
  <c-slot name="toolbar">
    <c-link href="/back" variant="history">Back</c-link>
  </c-slot>
  Content here
</c-module>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `class` | string | "" | Additional CSS classes |
| `filtered` | string | "false" | Adds filter styling class (use "true"/"false" strings) |

| Slot | Purpose |
|------|---------|
| `toolbar` | Action links, buttons at top |
| default | Main content area |

---

#### `<c-fieldset>`

Groups form fields with optional heading.

```django
<c-fieldset heading="Contact Information" aligned="true">
  <c-form_field label="Email" required="true">
    <input type="email" name="email" />
  </c-form_field>
</c-fieldset>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `heading` | string | "" | Section title (h2) |
| `class` | string | "" | Additional CSS classes |
| `aligned` | string | "false" | Enables aligned form layout for label-beside-input forms |

---

#### `<c-form_field>`

Single form field with label, help text, and error display.

```django
<c-form_field label="Title" required="true" help_text="Brief description" id="id_title">
  <input type="text" name="title" id="id_title" />
</c-form_field>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `label` | string | "" | Field label |
| `required` | string | "false" | Shows required indicator (use "true"/"false" strings) |
| `help_text` | string | "" | Help text below field |
| `error` | string | "" | Error message to display (singular, not list) |
| `id` | string | "" | For attribute on label |

---

#### `<c-button>`

Styled action button.

```django
<c-button type="submit" variant="primary">Save</c-button>
<c-button type="button" variant="danger">Delete</c-button>
<c-button type="button" variant="cancel">Cancel</c-button>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `type` | string | "button" | Button type (submit, button, reset) |
| `variant` | string | "default" | Style variant |
| `class` | string | "" | Additional CSS classes |
| `id` | string | "" | DOM id |
| `name` | string | "" | Form name attribute |
| `value` | string | "" | Form value attribute |

Note: Does not support `href` or `disabled` props. Use `<c-link>` for link-styled buttons.

| Variant | Appearance | Usage |
|---------|------------|-------|
| `default` | Standard button | Secondary actions |
| `primary` | Blue background (uses Django admin `.default` class) | Primary actions |
| `danger` | Orange background (--warning-fg) | Destructive actions |
| `cancel` | Cancel link style | Cancel/back actions |

---

#### `<c-link>`

Styled link with icon variants.

```django
<c-link href="/add" variant="add">Add New</c-link>
<c-link href="/back" variant="history">Back to List</c-link>
<c-link href="/edit" variant="change">Edit</c-link>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `href` | string | "#" | Link URL |
| `variant` | string | "default" | Style variant |
| `class` | string | "" | Additional CSS classes |
| `id` | string | "" | DOM id |

| Variant | CSS Class | Usage |
|---------|-----------|-------|
| `default` | (none) | Standard link |
| `add` | `addlink` | Add/create actions |
| `change` | `changelink` | Edit actions |
| `history` | `historylink` | Back/history links |

Note: Uses Django admin icon classes. No `delete` variant exists.

---

#### `<c-alert>`

Message display for feedback.

```django
<c-alert variant="success">Changes saved successfully.</c-alert>
<c-alert variant="error">
  <strong>Validation Errors:</strong>
  <ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>
</c-alert>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `variant` | string | "info" | Alert type |
| `class` | string | "" | Additional CSS classes |

| Variant | Background | Border | Text Color |
|---------|------------|--------|------------|
| `info` | #e8f4f8 | #417690 | #417690 |
| `success` | #e8f5e9 | #388e3c | #388e3c |
| `warning` | #fff3cd | #ffc107 | #856404 |
| `error` | #ffebee | #ba2121 | #ba2121 |
| (other) | #f5f5f5 | #999 | #333 |

---

#### `<c-badge>`

Status indicator badge.

```django
<c-badge status="open">Open</c-badge>
<c-badge status="resolved">Resolved</c-badge>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `status` | string | "default" | Badge type |
| `aria_label` | string | "" | Accessibility label (defaults to status title-cased) |

| Status Values | Background | Text Color | Border |
|---------------|------------|------------|--------|
| `open`, `info` | #e3f2fd | #1976d2 | #90caf9 |
| `claimed`, `warning`, `draft`, `distributing` | #fff3e0 | #f57c00 | #ffb74d |
| `resolved`, `success`, `completed`, `sent` | #e8f5e9 | #388e3c | #81c784 |
| `cancelled`, `pending` | #f5f5f5 | #757575 | #bdbdbd |
| `danger`, `failed` | #ffebee | #c62828 | #ef9a9a |
| (other) | #f5f5f5 | #333 | #ddd |

Note: In practice, badges are used for counts and labels in the codebase (e.g., "42 teams", "3 files").

---

#### `<c-table>`

Data table with accessibility features.

```django
<c-table id="result_list" aria_label="Team list" fixed_layout="true">
  <c-slot name="colgroup">
    <col style="width: 50%;">
    <col style="width: 50%;">
  </c-slot>
  <c-slot name="headers">
    <tr>
      <th scope="col"><div class="text">Team</div></th>
      <th scope="col"><div class="text">Status</div></th>
    </tr>
  </c-slot>
  {% for item in items %}
  <tr class="{% cycle 'row1' 'row2' %}">
    <td>{{ item.name }}</td>
    <td>{{ item.status }}</td>
  </tr>
  {% endfor %}
</c-table>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `id` | string | "result_list" | Table ID |
| `class` | string | "" | Additional CSS classes |
| `aria_label` | string | "Data table" | Accessibility label |
| `fixed_layout` | string | "false" | Use fixed column widths (use "true"/"false" strings) |

| Slot | Purpose |
|------|---------|
| `colgroup` | Column width definitions |
| `headers` | Table header row(s) in `<thead>` |
| default | Table body rows in `<tbody>` |

---

#### `<c-table_header>`

Sortable column header.

```django
<c-table_header sort_field="created_at" current_sort="-created_at" query_params="&status=open">
  Created
</c-table_header>
<c-table_header sortable="false">Non-sortable Column</c-table_header>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `sort_field` | string | "" | Field name for sorting |
| `current_sort` | string | "" | Current sort field (prefix `-` for desc) |
| `query_params` | string | "" | Additional URL params to preserve on sort |
| `sortable` | string | "true" | Whether column is sortable (use "true"/"false" strings) |

Note: No `min_width` prop exists. Displays ▲/▼ indicators for sort direction.

---

#### `<c-pagination>`

Page navigation for lists. Takes Django's paginator page object directly.

```django
<c-pagination :page_obj="page_obj" query_params="&status=open" />
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `page_obj` | object | "" | Django Paginator page object |
| `query_params` | string | "" | URL params to preserve on page links |

Displays "Showing X-Y of Z results" with First/Previous/Next/Last links.

---

#### `<c-filter_toolbar>`

Container for filter/search controls with htmx support.

```django
<c-filter_toolbar form_id="filter-form" hx_get="/list" hx_target="#results" hx_push_url="true">
  <c-filter_field label="Search" id="id_search">
    <input type="text" name="q" value="{{ request.GET.q }}" />
  </c-filter_field>
  <c-slot name="actions">
    <c-button type="submit">Filter</c-button>
  </c-slot>
</c-filter_toolbar>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `form_id` | string | "changelist-filter" | Form ID |
| `method` | string | "GET" | Form method |
| `action` | string | "" | Form action URL |
| `class` | string | "" | Additional CSS classes |
| `aria_label` | string | "Filter results" | Accessibility label |
| `hx_get` | string | "" | htmx GET endpoint |
| `hx_target` | string | "" | htmx target selector |
| `hx_swap` | string | "" | htmx swap mode |
| `hx_push_url` | string | "" | htmx push URL |

| Slot | Purpose |
|------|---------|
| default | Filter field components |
| `actions` | Buttons on the right side |

---

#### `<c-filter_field>`

Individual filter input in toolbar.

```django
<c-filter_field label="Team" id="id_team" min_width="200px">
  <select name="team" id="id_team">...</select>
</c-filter_field>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `label` | string | "" | Field label |
| `id` | string | "" | For attribute on label |
| `min_width` | string | "140px" | Minimum field width |

---

### Additional Components

#### `<c-stats_card>`

Dashboard metric display.

```django
<c-stats_card value="42" label="Pending Reviews" color="warning" />
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `value` | string | "" | Large number/value |
| `label` | string | "" | Description text |
| `color` | string | "primary" | Value color (primary, success, warning, danger) |

---

#### `<c-stats_card_grid>`

Grid container for stats cards.

```django
<c-stats_card_grid cols="3" gap="20px">
  <c-stats_card value="42" label="Pending" color="warning" />
  <c-stats_card value="18" label="Approved" color="success" />
</c-stats_card_grid>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `cols` | string | "3" | Number of columns |
| `gap` | string | "20px" | Spacing between cards |

---

#### `<c-detail_grid>`

Two-column detail layout with responsive breakpoint.

```django
<c-detail_grid gap="20px">
  <c-slot name="left">Left content</c-slot>
  <c-slot name="right">Right content</c-slot>
</c-detail_grid>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `gap` | string | "20px" | Spacing between columns |

Collapses to single column below 768px.

---

#### `<c-empty_state>`

Empty list/no results display.

```django
<c-empty_state icon="📋" title="No incident reports found" description="Incident reports will appear here.">
  <c-link href="/create" variant="add">Create One</c-link>
</c-empty_state>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `icon` | string | "" | Emoji or character |
| `title` | string | "" | Heading text |
| `description` | string | "" | Secondary text |

Default slot for optional action button.

---

#### `<c-nav>` and `<c-nav_item>`

Sub-navigation.

```django
<c-nav current="leaderboard">
  <c-nav_item name="leaderboard" href="{% url 'scoring:leaderboard' %}">Leaderboard</c-nav_item>
  {% if user.is_gold_team %}
  <c-nav_item name="red_team" href="{% url 'scoring:red_team_findings' %}">Review Red Team</c-nav_item>
  {% endif %}
</c-nav>
```

| `<c-nav>` Prop | Type | Default | Description |
|----------------|------|---------|-------------|
| `current` | string | "" | Name of current page for highlighting |

| `<c-nav_item>` Prop | Type | Default | Description |
|---------------------|------|---------|-------------|
| `name` | string | "" | Identifier to match against `current` |
| `href` | string | "#" | Link URL |

Note: `current` is not inherited from parent; must be passed to each nav_item or accessed via context.

---

#### `<c-score_value>`

Auto-colored score display.

```django
<c-score_value value="150" />
<c-score_value :value="points" format="signed" />
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `value` | string | "0" | Numeric value |
| `format` | string | "default" | Use "signed" to show +/- prefix |

Colors: positive (#28a745), negative (#dc3545), zero (#6c757d).

---

#### `<c-section_header>`

Flex header with title and actions.

```django
<c-section_header title="Recent Items" subtitle="Last 24 hours">
  <c-slot name="actions">
    <c-button variant="primary">Refresh</c-button>
  </c-slot>
</c-section_header>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `title` | string | "" | Heading text |
| `subtitle` | string | "" | Secondary text |
| `align` | string | "space-between" | Justify content value |

---

#### `<c-button_row>`

Container for form action buttons.

```django
<c-button_row class="justify-end mt-15">
  <c-button type="submit" variant="primary">Save</c-button>
  <c-link href="/cancel">Cancel</c-link>
</c-button_row>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `class` | string | "" | Additional classes (e.g., justify-end, mt-15) |

---

#### `<c-form_grid>`

Responsive grid for form fields.

```django
<c-form_grid min_width="200px" gap="15px">
  <div><label>Name</label><input type="text" /></div>
  <div><label>Email</label><input type="email" /></div>
</c-form_grid>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `min_width` | string | "200px" | Minimum column width |
| `gap` | string | "15px" | Grid gap |

---

#### `<c-image_grid>`

Responsive grid for images.

```django
<c-image_grid cols="3" gap="15px">
  <img src="/img1.png" />
  <img src="/img2.png" />
</c-image_grid>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `cols` | string | "3" | Number of columns |
| `gap` | string | "15px" | Grid gap |

Collapses to single column below 768px.

---

#### `<c-info_box>` and `<c-action_box>`

Content containers.

```django
<c-info_box variant="primary">Highlighted info</c-info_box>
<c-action_box heading="Actions" variant="warning">Form content</c-action_box>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `variant` | string | "default" | Style variant (default, primary, warning) |
| `class` | string | "" | Additional classes |
| `heading` | string | "" | (action_box only) Section heading |

---

#### `<c-selectable_table>`

Table with select-all checkbox functionality using Alpine.js.

```django
<c-selectable_table id="my_table" checkbox_name="item_ids" total_items="{{ items|length }}">
  <c-slot name="extra_headers">
    <th>Name</th>
    <th>Status</th>
  </c-slot>
  {% for item in items %}
  <tr data-selectable-id="{{ item.id }}">
    <td><input type="checkbox" x-model="selected" value="{{ item.id }}" /></td>
    <td>{{ item.name }}</td>
    <td>{{ item.status }}</td>
  </tr>
  {% endfor %}
</c-selectable_table>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `id` | string | "" | Table ID |
| `checkbox_name` | string | "selected_ids" | Name for row checkboxes |
| `aria_label` | string | "Selectable data table" | Accessibility label |
| `total_items` | string | "0" | Total selectable items count |

---

## Page Patterns

### List Page

```django
{% extends "admin/base_site.html" %}

{% block content %}
<div id="content-main">
  <c-page_header title="Tickets" />

  <c-module id="changelist" filtered="true">
    <c-slot name="toolbar">
      <c-link href="/tickets/create" variant="addlink">Create Ticket</c-link>
    </c-slot>

    <c-filter_toolbar>
      <c-filter_field label="Status">
        <select name="status">...</select>
      </c-filter_field>
    </c-filter_toolbar>

    <c-table aria_label="Ticket list">
      <c-slot name="headers">
        <tr>
          <c-table_header sort_field="id">ID</c-table_header>
          <th scope="col"><div class="text">Status</div></th>
        </tr>
      </c-slot>
      {% for ticket in tickets %}
      <tr class="{% cycle 'row1' 'row2' %}">
        <td>{{ ticket.id }}</td>
        <td><c-badge status="{{ ticket.status }}">{{ ticket.status }}</c-badge></td>
      </tr>
      {% empty %}
      <tr><td colspan="2">No tickets found.</td></tr>
      {% endfor %}
    </c-table>

    <c-pagination ... />
  </c-module>
</div>
{% endblock %}
```

---

### Detail Page

```django
{% extends "admin/base_site.html" %}

{% block content %}
<div id="content-main">
  <c-page_header title="Ticket #{{ ticket.id }}" />

  <c-module>
    <c-slot name="toolbar">
      <c-link href="/tickets" variant="history">Back to Tickets</c-link>
    </c-slot>

    <c-alert variant="info">Status: {{ ticket.status }}</c-alert>

    <c-fieldset heading="Ticket Details">
      <table class="detail-table">
        <tr><th>Team:</th><td>{{ ticket.team }}</td></tr>
        <tr><th>Category:</th><td>{{ ticket.category }}</td></tr>
        <tr><th>Created:</th><td>{{ ticket.created_at }}</td></tr>
      </table>
    </c-fieldset>
  </c-module>
</div>
{% endblock %}
```

---

### Form Page

```django
{% extends "admin/base_site.html" %}

{% block content %}
<div id="content-main">
  <c-page_header title="Create Ticket" />

  <c-module>
    <c-slot name="toolbar">
      <c-link href="/tickets" variant="history">Back to Tickets</c-link>
    </c-slot>

    {% if form.errors %}
    <c-alert variant="error">Please correct the errors below.</c-alert>
    {% endif %}

    <form method="POST">
      {% csrf_token %}
      <c-fieldset heading="Ticket Information" aligned="true">
        <c-form_field label="Title" required="true" error="{{ form.title.errors.0 }}">
          {{ form.title }}
        </c-form_field>
        <c-form_field label="Category" required="true">
          {{ form.category }}
        </c-form_field>
        <c-form_field label="Description" required="true" help_text="Describe your issue">
          {{ form.description }}
        </c-form_field>
      </c-fieldset>

      <c-button_row>
        <c-button type="submit" variant="primary">Create Ticket</c-button>
        <c-link href="/tickets">Cancel</c-link>
      </c-button_row>
    </form>
  </c-module>
</div>
{% endblock %}
```

---

### Dashboard Page

```django
{% extends "admin/base_site.html" %}

{% block content %}
<div id="content-main">
  <c-page_header title="Review Dashboard" />

  <c-module>
    <c-stats_card_grid cols="3">
      <c-stats_card value="{{ pending_count }}" label="Pending Review" color="warning" />
      <c-stats_card value="{{ approved_count }}" label="Approved" color="success" />
      <c-stats_card value="{{ rejected_count }}" label="Rejected" color="danger" />
    </c-stats_card_grid>
  </c-module>

  <c-module>
    <c-slot name="toolbar">
      <h2>Recent Items</h2>
    </c-slot>
    <c-table aria_label="Recent items">...</c-table>
  </c-module>
</div>
{% endblock %}
```

---

## Form Guidelines

### Standard Approach

Use Cotton components (`<c-fieldset>`, `<c-form_field>`) for all forms:

```django
<form method="POST">
  {% csrf_token %}
  <c-fieldset heading="Section Name" aligned="true">
    <c-form_field label="Field" required="true" help_text="Help">
      {{ form.field }}
    </c-form_field>
  </c-fieldset>
  <div class="submit-row">
    <c-button type="submit" variant="primary">Save</c-button>
  </div>
</form>
```

### Crispy Forms

No crispy forms usage found in templates. All forms use Cotton components.

### Form Layout Rules

1. One column layout (fields stacked)
2. Labels above inputs (aligned mode)
3. Required indicator on required fields
4. Help text below inputs
5. Error messages below inputs in red
6. Submit button row at bottom

---

## Table Guidelines

### Structure

```django
<c-table id="result_list" aria_label="Description" fixed_layout="true">
  <colgroup>
    <col style="width: 10%;">
    <col style="width: 30%;">
    <col style="width: 60%;">
  </colgroup>
  <c-slot name="headers">
    <tr>
      <th scope="col"><div class="text">ID</div></th>
      <th scope="col"><div class="text">Name</div></th>
      <th scope="col"><div class="text">Description</div></th>
    </tr>
  </c-slot>
  {% for item in items %}
  <tr class="{% cycle 'row1' 'row2' %}">
    <td>{{ item.id }}</td>
    <td>{{ item.name }}</td>
    <td>{{ item.description }}</td>
  </tr>
  {% endfor %}
</c-table>
```

### Column Width Guidelines

| Content Type | Width |
|--------------|-------|
| Checkbox | 5% |
| ID/Number | 8-10% |
| Status badge | 10-12% |
| Short text (name) | 15-20% |
| Email | 20-25% |
| Description | 30-40% |
| Actions | 10-15% |
| Timestamp | 15% |

### Row Styling

- Alternate row colors: `{% cycle 'row1' 'row2' %}`
- Field-specific classes: `class="field-status"`
- Numeric alignment: `style="text-align: right;"`

---

## Navigation Guidelines

### Primary Navigation

Rendered in header based on user roles. Links only shown if user has permission.

### Sub-Navigation

Uses `<c-nav>` and `<c-nav_item>` components. Used in:
- `scoring/base.html` - scoring section navigation
- `admin/base.html` - admin section navigation

```django
<c-nav current="leaderboard">
  <c-nav_item name="leaderboard" href="{% url 'scoring:leaderboard' %}">Leaderboard</c-nav_item>
  {% if user.is_gold_team %}
  <c-nav_item name="red_team" href="{% url 'scoring:red_team_findings' %}">Review Red Team</c-nav_item>
  {% endif %}
</c-nav>
```

### Current Page Indicator

Via inline styles in `nav_item.html`:
- Bold text (`font-weight: bold`)
- White color (`#fff`)
- Non-current links use `#447e9b`

---

## Accessibility Requirements

1. All tables have `aria-label`
2. All form inputs have associated `<label>`
3. Required fields marked with `required` attribute and visual indicator
4. Color not sole indicator of meaning (pair with text/icon)
5. Sufficient color contrast (4.5:1 minimum)
6. Keyboard navigation for all interactive elements
7. Focus indicators visible

---

## CSS Theming

### CSS Variables

All colors use CSS custom properties with fallbacks for dark mode support. Variables are defined in `base.html` and follow Django admin's theming system.

#### Available Variables

| Variable | Fallback | Usage |
|----------|----------|-------|
| `--body-bg` | `#fff` | Page background |
| `--body-fg` | `#333` | Main text color |
| `--body-quiet-color` | `#666` | Muted/secondary text |
| `--darkened-bg` | `#f8f8f8` | Card/panel backgrounds |
| `--hairline-color` | `#ddd` | Borders |
| `--link-fg` | `#447e9b` | Link color |
| `--link-hover-color` | `#036` | Link hover |
| `--primary` | `#417690` | Primary accent |
| `--primary-dark` | `#2e5266` | Primary hover/darker |
| `--error-fg` | `#ba2121` | Error/danger |
| `--success-fg` | `#28a745` | Success |
| `--header-link-color` | `#fff` | Navigation links |

#### Semantic Color Variables

Custom properties for semantic colors with dark mode support:

| Variable | Light Mode | Dark Mode | Usage |
|----------|------------|-----------|-------|
| `--info-bg` | `#e3f2fd` | `rgba(25, 118, 210, 0.15)` | Info backgrounds |
| `--info-fg` | `#1976d2` | — | Info text |
| `--info-border` | `#90caf9` | — | Info borders |
| `--warning-bg` | `#fff3e0` | `rgba(245, 124, 0, 0.15)` | Warning backgrounds |
| `--warning-fg` | `#f57c00` | — | Warning text |
| `--warning-border` | `#ffb74d` | — | Warning borders |
| `--success-bg` | `#e8f5e9` | `rgba(56, 142, 60, 0.15)` | Success backgrounds |
| `--success-border` | `#81c784` | — | Success borders |
| `--danger-bg` | `#ffebee` | `rgba(198, 40, 40, 0.15)` | Danger backgrounds |
| `--danger-border` | `#ef9a9a` | — | Danger borders |
| `--neutral-bg` | `#f5f5f5` | `rgba(117, 117, 117, 0.15)` | Neutral backgrounds |
| `--neutral-fg` | `#757575` | — | Neutral text |
| `--neutral-border` | `#bdbdbd` | — | Neutral borders |

#### Usage Pattern

```css
background: var(--primary, #417690);
color: var(--body-fg, #333);
border: 1px solid var(--hairline-color, #ddd);
```

#### Dark Mode

Dark mode is supported via `@media (prefers-color-scheme: dark)` rules. Components with dark mode overrides:
- `.alert--*` variants
- `.badge--*` variants
- `.warning-box`

### Component CSS Classes

Cotton components use CSS classes defined in `base.html` instead of inline styles:

| Component | CSS Classes |
|-----------|-------------|
| Alert | `.alert`, `.alert--info`, `.alert--success`, `.alert--warning`, `.alert--error`, `.alert--default` |
| Badge | `.badge`, `.badge--info`, `.badge--success`, `.badge--warning`, `.badge--neutral`, `.badge--danger`, `.badge--default` |
| Score | `.score-positive`, `.score-negative`, `.score-zero` |
| Stats Card | `.stats-card`, `.stats-card-value`, `.stats-card-label` |
| Navigation | `.nav-item`, `.nav-item--current` |
| Team Button | `.team-btn`, `.team-btn--selected` |
| Modal | `.modal`, `.modal--open`, `.modal-backdrop`, `.modal-content`, `.modal-header`, `.modal-close` |
| Visibility | `.d-none` (hide elements), `.d-block`, `.d-flex` |
| Filter | `.filter-actions` (toolbar button container) |

---

## Template Linting

Automated tests in `web/core/tests/test_template_lint.py` enforce these rules:

### Cotton Import Rule

All templates using Cotton components (`<c-*>` tags) must include the load statement:

```django
{% load cotton %}
```

Or combined with other tags:

```django
{% load static cotton %}
```

This is enforced by `TestCottonImports.test_cotton_components_have_load_statement`.

### No Inline Display Styles

Inline `display` styles are prohibited:

```html
<!-- Bad -->
<div style="display: none;">...</div>
<div style="display: flex; justify-content: space-between;">...</div>

<!-- Good -->
<div class="d-none">...</div>
<div class="d-flex justify-between">...</div>
```

JavaScript should use class toggling instead of `.style.display`:

```javascript
// Bad
element.style.display = 'none';
element.style.display = 'block';

// Good
element.classList.add('d-none');
element.classList.remove('d-none');
element.classList.add('modal--open');
```

**Exceptions:**
- Email templates (`templates/emails/`) - inline styles required for email clients
- Cotton component definitions (`templates/cotton/`) - may need inline styles for component implementation

This is enforced by `TestInlineStyles.test_no_inline_display_styles_in_html`.

### Cotton Component Rule

Use Cotton components instead of raw HTML for common patterns:

```html
<!-- Bad -->
<span class="badge badge-success">Active</span>
<span class="badge badge-warning">Pending</span>

<!-- Good -->
<c-badge status="success">Active</c-badge>
<c-badge status="warning">Pending</c-badge>
```

**Detected patterns:**
- `<span class="badge ...">` → use `<c-badge>`
- `<div class="stats-card ...">` → use `<c-stats_card>`
- `<div class="empty-state ...">` → use `<c-empty_state>`

**Exceptions:**
- Templates extending Django's `admin/base_site.html` (don't use Cotton)
- Email templates
- PDF templates
- Cotton component definitions

This is enforced by `TestCottonComponentUsage.test_no_raw_html_patterns`.

### Alpine.js Dynamic Bindings

When content requires Alpine.js reactivity (e.g., toast notifications), use raw HTML with Alpine's `:class` binding instead of Cotton components:

```html
<!-- Static status (known at server render) - use Cotton -->
<c-badge status="success">Active</c-badge>
<c-alert variant="error">Error message</c-alert>

<!-- Dynamic status (Alpine-controlled) - use raw HTML with CSS classes -->
<template x-if="message">
    <div class="alert" :class="messageType === 'success' ? 'alert--success' : 'alert--error'">
        <span x-text="message"></span>
    </div>
</template>
```

**When to use each approach:**

| Scenario | Approach |
|----------|----------|
| Status known at server render | Cotton component: `<c-badge status="success">` |
| Alpine-controlled dynamic state | Raw HTML with `:class` binding |
| Toast/notification with Alpine | Raw HTML inside `<template x-if>` |

**Why?** Cotton components render at server time, so they can't react to Alpine state changes. For reactive UI, use the CSS classes directly with Alpine's `:class` directive.

### Double Colon Syntax for Alpine Attributes

When passing Alpine dynamic attributes to Cotton components, use double colons (`::`) to prevent Cotton from interpreting them as component props:

```html
<!-- Single colon - Cotton interprets as prop (won't work for Alpine) -->
<c-button :disabled="loading">Save</c-button>

<!-- Double colon - passes through to rendered HTML for Alpine -->
<c-button ::disabled="loading">Save</c-button>
```

The double colon tells Cotton to pass the attribute through unchanged, resulting in:
```html
<button :disabled="loading">Save</button>
```

This allows Alpine.js to bind the `disabled` attribute reactively.

Reference: [Django Cotton - Alpine.js](https://django-cotton.com/docs/alpine-js)

---

## Migration Notes

When updating existing templates:

1. Replace `<div class="module">` with `<c-module>`
2. Replace inline alert divs with `<c-alert variant="...">`
3. Replace raw `<table>` with `<c-table>` (add `aria-label`)
4. Replace pipe-separated nav with `<c-nav>` and `<c-nav_item>`
5. Use `<c-stats_card_grid>` with `<c-stats_card>` for dashboards
6. Use `<c-button_row>` for form action buttons instead of `<div class="submit-row">`
7. Replace inline `style="display: ..."` with CSS classes (`.d-none`, `.d-flex`, etc.)
8. Replace JavaScript `.style.display` with `.classList.add()`/`.remove()`
