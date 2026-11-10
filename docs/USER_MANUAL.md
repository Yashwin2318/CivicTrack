# CivicTrack User Manual

CivicTrack has three portals. Which one you see depends on your account's role (Citizen, Officer or Admin); you cannot switch roles yourself. Every screen works on phones and desktops.

> Features described here exist in the current code. Until the new version is deployed, the live site offers only the original subset (see `FEATURE_STATUS.md`).

## Signing in

- **Create an account** (citizens): *New citizen? Create an account*, enter your name, email and a password with at least 8 characters, upper- and lower-case letters, a number and a symbol. Enter the code emailed to you.
- **Forgot password**: *Forgot your password?* → enter your email → enter the emailed code and a new password.
- **Staff**: administrators invite officers and admins; Cognito emails a temporary password, which you replace at first sign-in.
- For security you are signed out after 30 minutes without activity.

## Citizens

**Report an issue** (*Report an issue*, or tap a spot on the *Map* page):
1. Title, category and urgency. The form shows which department will handle it.
2. Address, optional landmark, and optionally the exact spot: *Use my current location*, tap the map, or search for a place.
3. Description (10+ characters) and an optional photo (JPG/PNG/WebP, ≤10 MB).
4. Press **Submit**. If similar open complaints exist within 200 m, or the description suggests a different category, CivicTrack shows this first: change the category or choose **Submit anyway**. Other people's complaint details are never shown, only distance and status.

You receive a tracking ID (e.g. `CT-1A2B3C4D5E`) and an email. Resubmitting after a network error will not create a duplicate. You can submit up to 10 complaints per hour (configurable).

**Track**: *My Complaints* (filters by status, category, date), *Track Complaint* (by ID) and *Map*. A complaint shows its department, ward/zone (if detected), officer (if the authority discloses names), SLA countdown, progress percentage, before/progress/after photos and a full timeline. *Email update* shows whether the last email was delivered.

**When resolved**: review the proof, then **Yes, close it** (optional star rating and feedback) or **No, reopen** with a reason. Reopening is allowed within 14 days of resolution, up to 3 times, and starts a new SLA period. If the authority closes a complaint without action (e.g. duplicate), the reason is shown.

**Notifications**: the bell lists items needing your attention and recent activity on your complaints from the last 30 days.

**Profile**: change your display name and password.

## Field officers

- **Dashboard**: active, awaiting-response, in-progress, overdue and completed counts; today's workload (assigned today, due within 24 h, resolved today); most urgent work by SLA.
- **New assignment**: **Accept**, or **Decline** with a reason (≥10 characters). Declined work returns to the administrator.
- **Start work** (optional before photo and note) → **Update progress** (note, % complete, optional photo) → **Resolve with proof** (after photo required, optional note). The citizen and administrator are notified.
- **Field Map**: your open jobs with category symbols. **Directions** opens Google Maps navigation. *Suggested visit order* sorts jobs nearest-next using straight-line distance; your position is used only in your browser and is **not** sent to the server or tracked.
- **SLA Monitor**: jobs ordered by time left. Warnings arrive before the deadline; breaches escalate automatically.
- **Work History**: completed jobs, average resolution time, SLA compliance and citizen ratings.
- **My Profile**: department, zones, wards, availability and workload limit set by the administrator.

You can only see complaints assigned to you.

## Administrators

- **Overview**: totals, pending assignment, SLA risks, department workload, open complaints on the map. Click a figure to open the filtered list.
- **Complaints**: filter by status, category, department, zone, ward, priority, SLA and date; export CSV. In a complaint:
  - **Assign / Reassign**: officers are ranked by department match, ward/zone coverage, nearby work, spare capacity and availability, with the reasons shown. Unavailable officers need an explicit override.
  - **Unassign**, **Classify** (priority, department, ward, zone), **Escalate** (raises priority and notifies), **Reject** (reason shown to the citizen, optional duplicate link) and **Close**.
  - The *Automated triage* panel explains the rule-based priority score and any category mismatch.
- **City Map**: category/status markers, clusters, heatmap, ward boundary shading (once boundaries are imported), place search.
- **Analytics & Reports**: date, department, zone, ward, category and priority filters; totals, resolution rate, average and median resolution time, SLA compliance, trends, distributions, department/officer/zone/ward performance, hotspots. **Summary CSV**, **Records CSV** and **Print / save PDF**.
- **Officers**: workload and performance; edit each officer's department, zones, wards, availability and maximum active complaints.
- **Users & Roles**: list users by role, **Invite staff**, change a role, enable or disable sign-in. You cannot change your own account. Role changes apply at the user's next sign-in.
- **Departments**: create/edit departments and map categories to departments (including new categories). *Save defaults now* stores the built-in set for editing.
- **Zones & Wards**: create zones and wards, or **Import boundaries (GeoJSON)**: choose the level, the file, and which properties hold the name, ID, ward number and zone; record the data source and licence. Invalid features are listed and skipped. With boundaries in place, new complaints with a map pin are located automatically. CivicTrack does not include any official boundary data.
- **Settings**: SLA hours (applies to new complaints), warning hours, complaints per citizen per hour, and whether officer names are shown to citizens.
- **Audit Log**: monthly list of administrative actions with actor and details.

## Email notifications

Sent for registration, assignment, acceptance, work start, resolution, reopening, rejection, escalation and SLA warnings/breaches. Emails contain only the complaint ID, title, category and status (staff emails add the location). While the AWS email service is in sandbox mode, only verified addresses receive mail; the complaint then shows "Not delivered".
