# User Roles and Stories

## Overview

WCComps Portal is a cybersecurity competition management platform that works alongside Quotient (the real-time scoring engine). WCComps Portal handles score adjustments, incident/finding tracking, and administrative functions while Quotient handles live uptime monitoring and service checks.

---

## System Architecture

### Scoring Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    DURING COMPETITION                            │
├────────────────────────────────┬────────────────────────────────┤
│  QUOTIENT (Real-time)          │  WCComps (Collection)          │
│  ─────────────────────         │  ───────────────────────       │
│  • Uptime monitoring           │  • Red findings submitted      │
│  • Service checks              │  • Blue incidents submitted    │
│  • Live scoring display        │  • Orange adjustments          │
│  • Inject problem delivery     │  • Inject grades               │
│  • Inject response collection  │  • Ticket resolutions          │
│                                │  • Gold/Admin spot-checks      │
└────────────────────────────────┴────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    COMPETITION END                               │
├─────────────────────────────────────────────────────────────────┤
│  FINAL SCORE = Quotient Base Score + WCComps Adjustments        │
│                                                                  │
│  Quotient Base:           WCComps Adjustments:                  │
│  • Uptime points          • Red findings approved (Blue -)      │
│  • Service points         • Incident matches (Blue + recovery)  │
│                           • Orange adjustments (+/-)            │
│                           • Inject grades (+)                   │
│                           • Ticket category points (+)          │
└─────────────────────────────────────────────────────────────────┘
```

### Unified Scoring Flow Pattern

All scoring inputs in WCComps follow the same pattern:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   SUBMIT    │ ──► │   REVIEW    │ ──► │   APPROVE   │ ──► │  AGGREGATE  │
│   (Form)    │     │   (Queue)   │     │  (Action)   │     │(End of Comp)│
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

| Type | Submitter | Reviewer | Review Process | Point Impact |
|------|-----------|----------|----------------|--------------|
| **Red Finding** | Red Team | Gold Team | Spot check, bulk approve | Blue Team loses points |
| **Blue Incident** | Blue Team | Gold Team | Review ALL, match to finding | Blue Team recovers points |
| **Orange Adjustment** | Orange Team | Gold Team | Spot check, bulk approve | +/- configurable points |
| **Inject Grade** | White Team | Gold Team | Spot check, bulk approve | + points per inject |
| **Ticket Resolution** | Ticketing Support | Ticketing Admin | Batch approve at end | Fixed points per category |

---

## User Roles

### Blue Team (Competition Participants)

**Identity**: Members of `WCComps_BlueTeam{N}` Authentik groups (e.g., `WCComps_BlueTeam01`)

Blue Team members are competition participants defending their assigned network infrastructure.

**Permissions**:
- Submit incident reports for their team
- View their team's incident reports
- Delete own incident reports before review
- Create and manage support tickets
- Cancel own open tickets (before claimed)
- View and download team packets
- Link Discord account to team

**Restrictions**:
- Cannot view leaderboard (use Quotient during competition)
- Cannot view other teams' data

---

### Red Team (Attackers)

**Identity**: Members of `WCComps_RedTeam` Authentik group

Red Team members simulate adversaries attacking Blue Team infrastructure.

**Permissions**:
- Submit red team findings (attack reports)
- View ALL red team findings (all members' submissions for coordination)
- Manage IP pools (create, edit, delete pools of source IPs for findings)
- Delete/leave own findings before approval

**Restrictions**:
- Cannot view leaderboard
- Cannot see if findings were matched to incidents
- Cannot see approval status of findings

---

### Gold Team (Competition Organizers/Judges)

**Identity**: Members of `WCComps_GoldTeam` Authentik group (or `WCComps_Discord_Admin` which inherits Gold Team permissions)

Gold Team members verify incidents, review findings, and ensure fair competition.

**Permissions**:
- View leaderboard
- Review and match ALL incident reports to red findings
- Spot-check and bulk approve red team findings
- Spot-check and bulk approve orange team adjustments
- Submit and approve inject grades
- Manage school information
- Competition management (start/end competition, manage teams, sync roles)
- Registration management (review, approve, reject, mark paid)
- Season and event management (create, edit, delete)
- Team assignment for events
- Upload and distribute team packets
- View team mappings (Discord ↔ Authentik)
- Manage student helpers
- Broadcast messages to teams

**Restrictions**:
- Cannot submit orange adjustments (can only review)

---

### White Team (Inject Graders)

**Identity**: Members of `WCComps_WhiteTeam` Authentik group

White Team members grade scenario-based inject submissions. Inject problems and responses are handled in Quotient; grades are submitted to WCComps.

**Permissions**:
- View leaderboard
- Submit inject grades (per-inject, per-team)
- View inject submission details (synced from Quotient)
- Review and match incident reports to red findings

**Restrictions**:
- Cannot approve inject grades (Gold Team reviews)
- Cannot approve red team findings
- Cannot submit orange adjustments

---

### Orange Team (Bonus/Penalty Adjustments)

**Identity**: Members of `WCComps_OrangeTeam` Authentik group

Orange Team awards bonus points or penalties for behavior, task completion, or rule violations.

**Permissions**:
- Submit point adjustments with dynamic check types
- View their own submitted adjustments

**Check Types**: Managed via `OrangeCheckType` model with configurable default point values. Examples:
- Customer service call answered
- Network diagram completed
- Password reset assistance
- Rule violation (negative points)
- Professional behavior bonus

Check types are created via Django Admin, not dynamically on first use.

**Restrictions**:
- Cannot approve their own adjustments (Gold Team reviews)
- Cannot view leaderboard

---

### Ticketing Support

**Identity**: Members of `WCComps_Ticketing_Support` Authentik group

Support staff handle participant support tickets during competition.

**Permissions**:
- View all tickets (list and detail)
- Claim tickets (assign to self)
- Unclaim tickets (release assignment)
- Resolve tickets (category determines point value)
- Add comments and attachments to tickets

**Restrictions**:
- Cannot view leaderboard
- Cannot perform bulk operations
- Cannot reassign to others
- Cannot approve ticket points (Ticketing Admin does batch approval)

---

### Ticketing Admin

**Identity**: Members of `WCComps_Ticketing_Admin` Authentik group

Ticket administrators have elevated permissions for ticket management.

**Permissions**:
- All Ticketing Support permissions
- View leaderboard
- Bulk claim/resolve multiple tickets
- Reassign tickets to other users
- Change ticket category
- Reopen resolved tickets
- Batch approve ticket points at competition end
- Clear all tickets (administrative reset)

---

### System Admin

**Identity**: Members of `WCComps_Discord_Admin` Authentik group

System administrators have full access to system configuration.

**Permissions**:
- All Gold Team permissions
- All Ticketing Admin permissions
- Django admin access
- Competition configuration (timing, parameters)
- Sync operations with Quotient
- Score recalculation
- Data export (CSV/JSON)
- Team registration management

---

## Role-Feature Matrix

| Feature | Blue | Red | Gold | White | Orange | Ticket Supp | Ticket Admin | Admin |
|---------|------|-----|------|-------|--------|-------------|--------------|-------|
| View Leaderboard | | | ✓ | ✓ | | | ✓ | ✓ |
| Submit Incident | ✓ | | | | | | | ✓ |
| View Own Incident Reports | ✓ | | ✓ | | | | | ✓ |
| Submit Red Finding | | ✓ | | | | | | ✓ |
| View All Red Findings | | ✓ | ✓ | | | | | ✓ |
| Review/Match Incident Reports | | | ✓ | ✓ | | | | ✓ |
| Approve Red Findings | | | ✓ | | | | | ✓ |
| Submit Inject Grade | | | ✓ | ✓ | | | | ✓ |
| Approve Inject Grades | | | ✓ | | | | | ✓ |
| Submit Orange Adjustment | | | | | ✓ | | | ✓ |
| Approve Orange Adjust | | | ✓ | | | | | ✓ |
| View Team Mappings | | | ✓ | | | | | ✓ |
| Manage School Info | | | ✓ | | | | | ✓ |
| Manage IP Pools | | ✓ | | | | | | ✓ |
| Create Tickets | ✓ | | ✓ | | | | | ✓ |
| Cancel Own Tickets | ✓ | | | | | | | |
| View All Tickets | | | | | | ✓ | ✓ | ✓ |
| Claim/Resolve Tickets | | | | | | ✓ | ✓ | ✓ |
| Bulk Ticket Operations | | | | | | ✓ | ✓ | ✓ |
| Approve Ticket Points | | | | | | | ✓ | ✓ |
| View Team Packets | ✓ | | ✓ | | | | | ✓ |
| Upload/Distribute Packets | | | ✓ | | | | | ✓ |
| Link Discord Account | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Comp Mgmt | | | ✓ | | | | | ✓ |
| Team Mgmt | | | ✓ | | | | | ✓ |
| Manage Helpers | | | ✓ | | | | | ✓ |
| Broadcast Messages | | | ✓ | | | | | ✓ |
| Sync Discord Roles | | | ✓ | | | | | ✓ |
| Review Registrations | | | ✓ | | | | | ✓ |
| Manage Seasons/Events | | | ✓ | | | | | ✓ |
| Assign Teams to Events | | | ✓ | | | | | ✓ |
| Export Data | | | | | | | | ✓ |
| Black Team Adjustments | | | | | | | | ✓ |
| Django Admin | | | | | | | | ✓ |

### View-Level Permissions (URL Access)

| View                    | Blue | Red | Gold | White | Orange | Ticket Supp | Ticket Admin | Admin |
|-------------------------|------|-----|------|-------|--------|-------------|--------------|-------|
| Public Registration     | ✓**  | ✓** | ✓**  | ✓**   | ✓**    | ✓**         | ✓**          | ✓**   |
| Registration Review     |      |     | ✓    |       |        |             |              | ✓     |
| Season/Event Mgmt       |      |     | ✓    |       |        |             |              | ✓     |
| Discord Link            | ✓    | ✓   | ✓    | ✓     | ✓      | ✓           | ✓            | ✓     |
| Leaderboard             |      |     | ✓    | ✓     |        |             | ✓            | ✓     |
| Red Team Portal         |      | ✓   | ✓    |       |        |             |              | ✓     |
| Incident Submission     | ✓*   |     |      |       |        |             |              | ✓     |
| Review Incidents        |      |     | ✓    | ✓     |        |             |              | ✓     |
| Orange Team Portal      |      |     | ✓    |       | ✓      |             |              | ✓     |
| Inject Grading          |      |     | ✓    | ✓     |        |             |              | ✓     |
| Review Inject Grades    |      |     | ✓    |       |        |             |              | ✓     |
| Team Tickets            | ✓    |     |      |       |        |             |              |       |
| Create Ticket           | ✓    |     | ✓    |       |        |             |              | ✓     |
| Review Tickets          |      |     |      |       |        |             | ✓            | ✓     |
| Ops Tickets             |      |     |      |       |        | ✓           | ✓            | ✓     |
| Team Packets            | ✓    |     |      |       |        |             |              |       |
| Ops Packets             |      |     | ✓    |       |        |             |              | ✓     |
| Team Mappings           |      |     | ✓    |       |        |             |              | ✓     |
| Ops School Info         |      |     | ✓    |       |        |             |              | ✓     |
| Comp Mgmt               |      |     | ✓    |       |        |             |              | ✓     |
| Team Mgmt               |      |     | ✓    |       |        |             |              | ✓     |
| Helpers Mgmt            |      |     | ✓    |       |        |             |              | ✓     |
| Broadcast               |      |     | ✓    |       |        |             |              | ✓     |
| Sync Roles              |      |     | ✓    |       |        |             |              | ✓     |
| Export Views            |      |     |      |       |        |             |              | ✓     |

\* Blue Team can submit incidents only if they have a team assigned

\*\* Public registration is accessible without authentication

---

## User Stories

### Registration Stories (Pre-Competition)

#### REG-1: Register Team for Competition
> As a **coach or team captain**, I want to **register my team for the competition** so that I can **secure a spot and receive account credentials**.

**Acceptance Criteria**:
- Public registration form (no authentication required)
- Form captures school name, contact details (captain, coach)
- Select events to enroll in from active season
- Confirmation shown with next steps
- Edit link with secure token sent via email

#### REG-2: Edit Registration
> As a **registrant**, I want to **edit my registration** so that I can **update contact info or event selections**.

**Acceptance Criteria**:
- Access via secure token link (sent in confirmation email)
- Can update school name, contacts, event enrollments
- Cannot edit after credentials are sent
- Token can have expiration date

#### REG-3: Review Team Registrations
> As a **Gold Team member**, I want to **review and manage team registrations** so that I can **verify eligibility and track payment**.

**Acceptance Criteria**:
- List of all registrations with status filter
- View registration details and contacts
- Approve or reject with reason
- Mark registration as paid
- Status workflow: `pending` → `approved` → `paid` → `credentials_sent` (or `rejected`)

#### REG-4: Manage Seasons
> As a **Gold Team member**, I want to **manage competition seasons** so that I can **organize events by year**.

**Acceptance Criteria**:
- Create, edit, delete seasons
- Set active season
- View events within each season

#### REG-5: Manage Events
> As a **Gold Team member**, I want to **manage events within a season** so that I can **set up competition dates and registration limits**.

**Acceptance Criteria**:
- Create, edit, delete events
- Set event type (invitational, qualifier, regional, state)
- Set date, start/end times
- Set max teams and registration deadline
- Open/close registration
- View enrollment counts

#### REG-6: Assign Teams to Event
> As a **Gold Team member**, I want to **assign team numbers to registrations** so that I can **prepare for competition day**.

**Acceptance Criteria**:
- View event details with enrolled registrations
- Randomly assign team numbers to paid registrations
- Only assign to registrations with "paid" status
- Unassign teams before credentials are sent
- Track which registrations have received credentials

---

### Blue Team Stories

#### BT-1: Report Security Incident
> As a **Blue Team member**, I want to **submit an incident report** so that I can **document attacks I've detected and recover points**.

**Acceptance Criteria**:
- Form captures: target box, source IP, services affected, attack type, description
- Box selection auto-populates destination IP and available services
- Upload screenshot evidence (multiple files supported)
- Can only submit for my team's infrastructure
- Confirmation shown after submission

#### BT-2: View My Incident Reports
> As a **Blue Team member**, I want to **view my team's incident reports** so that I can **see what we've submitted and avoid duplicates**.

**Acceptance Criteria**:
- List of all incidents submitted by my team
- Shows submission time, box, attack type
- Detail view for each incident
- Status NOT shown (Blue doesn't see match status)

#### BT-2a: Delete Incident Report
> As a **Blue Team member**, I want to **delete my own incident report** so that I can **correct mistakes before review**.

**Acceptance Criteria**:
- Can delete own reports before Gold Team review
- Cannot delete after review
- Confirmation required before deletion

#### BT-3: Request Support
> As a **Blue Team member**, I want to **create a support ticket** so that I can **get help with technical issues**.

**Acceptance Criteria**:
- Ticket creation with category selection
- Can cancel open tickets before they are claimed

#### BT-4: Link Discord Account
> As a **Blue Team member**, I want to **link my Discord account** so that I can **receive team role and access Discord channels**.

**Acceptance Criteria**:
- Use /link command in Discord to get secure link
- Authenticate via Authentik OAuth
- Discord role assigned automatically
- Team membership tracked
- Team size limits enforced

#### BT-5: View Team Packets
> As a **Blue Team member**, I want to **view and download team packets** so that I can **access competition materials**.

**Acceptance Criteria**:
- List of available packets for my team
- Download packet files
- Only see packets enabled for web access
- Shows upload date and description

---

### Red Team Stories

#### RT-1: Document Attack
> As a **Red Team member**, I want to **submit a finding** so that I can **document successful attacks**.

**Acceptance Criteria**:
- Form captures: target team, target box, source IP, attack type, description
- Box selection auto-populates destination IP and services
- Attack type autocomplete from previous submissions (XSS-safe)
- Upload screenshot evidence (multiple files supported)
- Select outcome checkboxes (root access, credentials recovered, etc.)
- Confirmation shown after submission

#### RT-2: View All Findings
> As a **Red Team member**, I want to **view all red team findings** so that I can **coordinate with other red team members and avoid duplicates**.

**Acceptance Criteria**:
- List of ALL findings from ALL red team members
- Filter by team targeted, attack type, submitter
- Shows submission time, target, attack type
- Approval status NOT shown

#### RT-3: Manage IP Pools
> As a **Red Team member**, I want to **manage IP pools** so that I can **group source IPs and associate findings with them**.

**Acceptance Criteria**:
- Create IP pools with name and list of IPs
- Edit existing pools
- Delete pools (if not in use by findings)
- Select pool when submitting findings

#### RT-4: Delete Finding
> As a **Red Team member**, I want to **delete my own findings** so that I can **correct mistakes before approval**.

**Acceptance Criteria**:
- Delete own findings before approval
- Cannot modify after approval

---

### Gold Team Stories

#### GT-1: View Leaderboard
> As a **Gold Team member**, I want to **view the leaderboard** so that I can **monitor competition standings**.

**Acceptance Criteria**:
- Leaderboard displays all teams with rankings
- Shows current aggregated score
- Shows score breakdown by category

#### GT-2: Review and Match Incident Reports
> As a **Gold Team member**, I want to **review ALL incident reports and match them to red findings** so that I can **verify defensive detections**.

**Acceptance Criteria**:
- Queue of ALL incident reports (must review each one)
- View incident details
- See suggested red finding matches (by source IP, timing)
- Match incident to specific red finding
- Reject incident if invalid
- Track review progress

#### GT-3: Spot-Check Red Findings
> As a **Gold Team member**, I want to **spot-check red team findings** so that I can **verify data looks correct before bulk approval**.

**Acceptance Criteria**:
- List of pending red findings
- Quick scan view (team, attack type, timestamp)
- Flag suspicious entries for detailed review
- Bulk approve all unflagged entries

#### GT-4: Review Orange Adjustments
> As a **Gold Team member**, I want to **review orange team adjustments** so that I can **approve or reject point changes**.

**Acceptance Criteria**:
- List of pending adjustments
- Shows team, check type, point value (+/-), reason, submitter
- Approve or reject each (or bulk approve)
- Rejected items notify Orange Team submitter

#### GT-5: Review Inject Grades
> As a **Gold Team member**, I want to **spot-check inject grades** so that I can **verify grading consistency**.

**Acceptance Criteria**:
- List of submitted grades by inject and team
- Spot-check for outliers or suspicious values
- Bulk approve when satisfied

#### GT-6: Monitor Team Mappings
> As a **Gold Team member**, I want to **view team mappings** so that I can **see participant assignments**.

**Acceptance Criteria**:
- List all teams with member count
- Show Discord/Authentik usernames
- Search/filter by team number or name

#### GT-7: Manage School Information
> As a **Gold Team member**, I want to **manage school contact information** so that I can **communicate with coaches**.

**Acceptance Criteria**:
- View/edit school name, contact emails per team
- Import from CSV
- Track last updated

#### GT-8: Competition Management
> As a **Gold Team member**, I want to **manage competition settings** so that I can **control competition flow**.

**Acceptance Criteria**:
- Set competition start/end times (with timezone support)
- Configure controlled applications (Authentik apps)
- Set max team members
- Start competition (enables apps + blue team accounts)
- End competition (deactivates links, disables accounts, clears helpers)
- Enable/disable all blue team accounts
- Reset team passwords (generates CSV download)
- Sync Discord roles from Authentik
- Broadcast messages to teams

#### GT-8a: Team Management
> As a **Gold Team member**, I want to **manage individual teams** so that I can **handle team-specific issues**.

**Acceptance Criteria**:
- View team details and member list
- Activate/deactivate individual teams
- Unlink individual users from team
- Reset team (unlinks all users, resets password, revokes sessions)
- Recreate Discord channels for team
- Bulk activate/deactivate teams
- Bulk recreate channels for multiple teams

#### GT-9: Upload and Distribute Packets
> As a **Gold Team member**, I want to **upload and distribute team packets** so that I can **share competition materials with all teams**.

**Acceptance Criteria**:
- Upload packet files (max 25 MB)
- Set title and notes for packet
- Choose email/web distribution methods
- Trigger distribution to all teams
- View distribution status per team
- Track download counts
- Cancel packet distribution

#### GT-10: Manage Helpers
> As a **Gold Team member**, I want to **manage student helpers** so that I can **grant temporary Discord roles**.

**Acceptance Criteria**:
- Add helpers by Discord ID
- Remove helpers
- Assign helper roles in Discord

---

### White Team Stories

#### WT-1: Submit Inject Grades
> As a **White Team member**, I want to **submit grades for inject responses** so that I can **score teams on business tasks**.

**Acceptance Criteria**:
- List of injects with grading status
- Select inject, select team
- Enter score (and optionally max points)
- Track grading progress (X of Y teams graded)

#### WT-2: View Leaderboard
> As a **White Team member**, I want to **view the leaderboard** so that I can **see standings**.

**Acceptance Criteria**:
- Access to leaderboard
- Can see overall rankings

#### WT-3: Review and Match Incidents
> As a **White Team member**, I want to **review and match incident reports** so that I can **help verify defensive detections**.

**Acceptance Criteria**:
- View all incident reports
- See suggested red finding matches
- Match incident to specific red finding
- Award recovery points

---

### Orange Team Stories

#### OT-1: Submit Point Adjustment
> As an **Orange Team member**, I want to **submit a point adjustment** so that I can **recognize performance or penalize violations**.

**Acceptance Criteria**:
- Select team
- Select or create check type (dynamic list)
- Enter point value (positive or negative)
- Provide reason/justification
- Submission goes to Gold Team for review
- View my submitted adjustments and their status

---

### Ticketing Stories

#### TS-1: View Ticket Queue
> As a **Ticketing Support member**, I want to **view all tickets** so that I can **identify issues needing attention**.

**Acceptance Criteria**:
- List with status filter (open, claimed, resolved, cancelled)
- Sort by age, team, category, assignee
- Search by ticket number, title, description
- Stale ticket indicators

#### TS-2: Work Ticket
> As a **Ticketing Support member**, I want to **claim and resolve tickets** so that I can **help participants**.

**Acceptance Criteria**:
- Claim ticket (assigns to me)
- Add comments visible to participant
- Add attachments
- Unclaim if needed
- Reassign to another support member
- Change ticket category
- Resolve (points auto-assigned by category)

#### TA-1: Bulk Operations
> As a **Ticketing Admin**, I want to **perform bulk operations** so that I can **efficiently manage high volumes**.

**Acceptance Criteria**:
- Select multiple tickets
- Bulk claim or resolve
- Reopen resolved tickets
- Clear all tickets and reset counters
- Confirmation of results

#### TA-2: Approve Ticket Points
> As a **Ticketing Admin**, I want to **batch approve ticket points at competition end** so that I can **finalize ticket scoring**.

**Acceptance Criteria**:
- View all resolved tickets with point values
- Verify points by category are correct
- Bulk approve for final aggregation

---

### Admin Stories

#### AD-1: Configure Competition
> As a **System Admin**, I want to **configure competition settings** so that I can **control timing and parameters**.

**Acceptance Criteria**:
- Set competition start/end times
- Set registration deadline and capacity
- Configure controlled applications (Authentik)
- Set max team members

#### AD-2: Sync with Quotient
> As a **System Admin**, I want to **sync data with Quotient** so that I can **ensure consistency**.

**Acceptance Criteria**:
- Sync metadata (boxes, services, injects)
- View last sync time
- Manual sync trigger

#### AD-3: Export Data
> As a **System Admin**, I want to **export all scoring data** so that I can **analyze and archive competition results**.

**Acceptance Criteria**:
- Export red findings (CSV/JSON)
- Export incidents (CSV/JSON)
- Export orange adjustments (CSV/JSON)
- Export inject grades (CSV/JSON)
- Export ticket resolutions (CSV/JSON)
- Export final aggregated scores

#### AD-4: Aggregate Final Scores
> As a **System Admin**, I want to **trigger final score aggregation** so that I can **produce competition results**.

**Acceptance Criteria**:
- Verify all reviews complete (incidents matched, approvals done)
- Trigger aggregation
- Combine Quotient base + WCComps adjustments
- Generate final leaderboard

#### AD-5: Black Team Adjustments
> As a **System Admin**, I want to **make manual point adjustments** so that I can **handle special cases and corrections**.

**Acceptance Criteria**:
- Create point adjustments (positive or negative) via Django Admin
- Specify team, reason, and point value
- Adjustments included in final score calculation
- Full audit trail of who made adjustment and when

---

### Team Packet Stories

#### TP-1: Upload Packets
> As a **System Admin**, I want to **upload packets for teams** so that I can **distribute competition materials**.

**Acceptance Criteria**:
- Upload file with description
- Assign to specific team or all teams
- Track upload date

#### TP-2: Download Packets
> As a **Blue Team member**, I want to **download my team's packets** so that I can **access materials**.

**Acceptance Criteria**:
- List packets available to my team
- Secure download
- Cannot access other teams' packets

---

### Discord Integration Stories

#### DI-1: Link Discord Account
> As a **participant**, I want to **link my Discord account** so that I can **use Discord bot features**.

**Acceptance Criteria**:
- Initiate from Discord bot command
- OAuth flow to authenticate
- Confirmation of link
- Can unlink/relink

#### DI-2: Receive Ticket Notifications
> As a **Blue Team member**, I want to **receive Discord notifications** so that I can **respond to ticket updates**.

**Acceptance Criteria**:
- Notification when claimed, commented, resolved
- Direct message or team channel

#### DI-3: Manage Tickets via Discord
> As a **Ticketing Support**, I want to **manage tickets via Discord** so that I can **work without switching to web**.

**Acceptance Criteria**:
- View ticket details via command
- Claim/resolve via command
- Add comments via command

---

### Competition Lifecycle Stories

#### CL-1: Pre-Competition Setup
> As a **System Admin**, I want to **set up the competition** so that I can **prepare before participants arrive**.

**Acceptance Criteria**:
- Import team/school info
- Sync metadata from Quotient
- Verify Authentik groups
- Test integrations

#### CL-2: Start Competition
> As a **System Admin**, I want to **start the competition** so that I can **enable participant access**.

**Acceptance Criteria**:
- Manual start via Discord bot
- Participants gain remote access and Quotient access

#### CL-3: End Competition
> As a **System Admin**, I want to **end the competition** so that I can **freeze submissions**.

**Acceptance Criteria**:
- Manual end via Discord bot
- Remote access and Quotient access disabled
- Reviews continue in web UI after competition ends

#### CL-4: Publish Results
> As a **System Admin**, I want to **publish final results** so that I can **share outcomes**.

**Acceptance Criteria**:
- Generate final standings
- Export results
- Optionally reveal to participants

---

## Navigation Requirements

### Primary Navigation (Header)

Defined in `templates/admin/base_site.html`:

| Link | Condition | Destination |
|------|-----------|-------------|
| Tickets | `is_ticketing_support` or `is_ticketing_admin` | `/ops/tickets/` |
| Incident Report | `is_blue_team` or `is_admin` | `/scoring/incident/submit/` |
| Red Team Findings | `is_red_team` or `is_admin` | `/scoring/red-team/submit/` |
| Orange Team | `is_orange_team` or `is_admin` | `/scoring/orange-team/` |
| Scoring | `is_gold_team` or `is_white_team` or `is_ticketing_admin` or `is_admin` | `/scoring/leaderboard/` |
| School Info | `is_gold_team` or `is_admin` | `/ops/school-info/` |
| Admin | `is_admin` (Authentik group) | `/admin/` |
| Comp Mgmt | `is_gold_team` or `is_admin` | `/ops/admin/competition/` |

### Scoring Sub-Navigation

Defined in `templates/scoring/base.html`:

| Link | Condition |
|------|-----------|
| Leaderboard | Always (if can access scoring) |
| Review Red Team | `is_gold_team` or `is_admin` |
| Review Orange Team | `is_gold_team` or `is_admin` |
| Review Incidents | `is_gold_team` or `is_admin` |
| Review Injects | `is_gold_team` or `is_admin` |
| Review Tickets | `is_ticketing_admin` or `is_admin` |
| Inject Grading | `is_white_team` or `is_gold_team` or `is_admin` |
| Configuration | `is_admin` |
| Export Data | `is_admin` |

### Landing Pages (Home Redirect)

When users access `/` or complete OAuth login, they are redirected based on role priority:

| Priority | Role Check | Destination |
|----------|------------|-------------|
| 1 | Red Team | `/scoring/red-team/submit/` |
| 2 | Orange Team | `/scoring/orange-team/` |
| 3 | Blue Team | `/tickets/` (team ticket list) |
| 4 | Ticketing Support, Ticketing Admin, or System Admin | `/ops/tickets/` |
| 5 | Gold Team | `/scoring/leaderboard/` |
| 6 | Fallback (all others) | `/scoring/leaderboard/` |

---

## Authentik Group Summary

| Group Name | Role |
|------------|------|
| `WCComps_BlueTeam{N}` | Blue Team (N = 01-50) |
| `WCComps_RedTeam` | Red Team |
| `WCComps_GoldTeam` | Gold Team |
| `WCComps_WhiteTeam` | White Team |
| `WCComps_OrangeTeam` | Orange Team |
| `WCComps_BlackTeam` | Black Team (operations/support staff) |
| `WCComps_Ticketing_Support` | Ticketing Support |
| `WCComps_Ticketing_Admin` | Ticketing Admin |
| `WCComps_Discord_Admin` | System Admin (inherits Gold Team) |

---

## Data Export Requirements

Exports available at `/scoring/export/` (Admin only). All exports support CSV and JSON formats via `?format=csv` or `?format=json`.

| Endpoint | Data Type |
|----------|-----------|
| `/export/red-findings/` | Red Team Findings |
| `/export/incidents/` | Blue Team Incident Reports |
| `/export/orange-adjustments/` | Orange Team Adjustments |
| `/export/inject-grades/` | Inject Grades |
| `/export/final-scores/` | Final Aggregated Scores |

Note: Ticket resolutions export not implemented as a separate endpoint.

---

## Implementation Status

### Implemented Features
- Leaderboard restricted to Gold/White Team, Ticketing Admin, and System Admin
- Navigation shows only accessible links based on user permissions
- Team registration flow with status workflow (`pending` → `approved` → `paid` → `credentials_sent`)
- Public registration form (no authentication required)
- Token-based self-service registration editing
- Season and event management (Gold Team)
- Per-event team number assignment with random shuffle
- Data export functionality (CSV/JSON for red findings, incidents, orange adjustments, inject grades, final scores)
- Orange Team check types managed via `OrangeCheckType` model
- Inject grading UI at `/scoring/inject-grading/` (White and Gold Team)
- Batch approval workflows for inject grades and red findings
- Competition management UI at `/ops/admin/competition/` (Gold Team and Admin)
- Red Team IP pool management
- Red Team finding submission
- Incident review accessible by White Team (in addition to Gold Team)
- Discord role sync from Authentik groups
- Discord account linking via OAuth (team accounts with membership limits)
- Team packets upload, distribution, and download
- Team mappings view showing Discord ↔ Authentik links
- Helper management for temporary Discord roles
- Broadcast messaging to teams
- School info CSV import
- Ticket points verification workflow
- Ticket category change by support staff
- Team management (activate/deactivate, unlink users, reset, recreate channels)
- Bulk team operations (activate/deactivate/recreate for multiple teams)
- Session revocation on team reset
- Password reset with CSV export
- Screenshot/evidence uploads for incidents and red findings
- Black team adjustments via Django Admin
- Red team finding outcome checkboxes (auto-calculated points)

---

## Resolved Decisions

| Question | Answer |
|----------|--------|
| Inject grading scale | Points vary per inject; White Team enters score AND optionally max points |
| Email notifications | WCComps sends emails (SMTP) for: packets, passwords, registration/payment info |
| Data retention | Admin-triggered archive + delete from live system; past results not viewable after archive |
| Ticket category points | Hardcoded per category (see table below) |
| Incident↔Finding matching | Primarily one-to-one; allow flexibility for edge cases |
| Rejected items | Just skipped (stays in system, not matched/approved) |
| In-person competition | Authentik-only auth; Discord features disabled |

---

## Ticket Category Point Values

| Category | Points | Notes |
|----------|--------|-------|
| Service Scoring Validation | 0 | Free initially, tracked for abuse (5pt penalty if misused) |
| Box Reset / Scrub | 60 | Fixed |
| Scoring Service Check | 10 | Fixed |
| Black Team Phone Consultation | 100 | Fixed |
| Black Team Hands-on Consultation | 200 | 300 if consultation exceeded 45 minutes |
| Other / General Issue | 0 | Ticket lead manually adjusts points if needed |
