# CivicTrack User Manual

CivicTrack has three portals. Which one you see depends on your account's role; you cannot switch roles yourself.

## Citizens

**Create an account.** On the sign-in page choose *New citizen? Create an account*, enter your email and a password (8+ characters with upper and lower case, a number and a symbol), then enter the code emailed to you.

**Report an issue.** Select **Report an issue** and fill in:
1. A short title (e.g. "Streetlight not working").
2. Category — Roads, Sanitation, Streetlights, Waterlogging, Water Supply or Other.
3. Address/area and, optionally, a nearby landmark.
4. Optionally pin the exact spot: tap **Use my current location** (your browser will ask permission) or tap the map.
5. A description (at least 10 characters).
6. Optionally a photo (JPG, PNG or WebP, up to 10 MB).

Submit. You'll see your **tracking ID** (e.g. `CT-1A2B3C4D5E`) and receive a confirmation email. If the connection drops and you press submit again, CivicTrack will not create a duplicate.

**Track progress.** *My Complaints* lists everything you reported, with filters for status, category and date. *Track Complaint* finds one by ID. Open any complaint to see its details, map, photos and a **status timeline**: Pending → Assigned → In Progress → Resolved → Closed. The *Email update* line shows whether the last email to you was delivered.

**When it's resolved.** You'll get an email and see the officer's proof photo. Choose:
- **Yes, close it** — confirms the fix (this cannot be undone), or
- **No, reopen** — explain what's still wrong. The department is notified and a new 48-hour target starts.

## Field officers

Accounts are created by the administrator. Your **Dashboard** shows active work, items not started, items in progress, overdue items and work you've completed; *Most urgent* lists jobs by time left.

- **Start work**: optional "before" photo and note. Allowed once a complaint is assigned to you (or reopened).
- **Resolve with proof**: an "after" photo is required, plus an optional note. The citizen and administrator are notified.
- **Directions** opens Google Maps navigation to the pinned location (or a search for the address when there is no pin).
- **Field Map** shows your open jobs. *Suggested visit order* sorts them nearest-next from your current position using straight-line distance — it is a planning aid, not road routing or tracking.
- **SLA Monitor**: every complaint has a 48-hour target. Items turn amber in the last 4 hours and red when overdue; you and the administrator are emailed once at each stage.
- **Work History** shows completed jobs, your average resolution time and SLA compliance.

You can only see complaints assigned to you.

## Administrators

- **Overview**: totals, items needing assignment, SLA risks, department performance and a map of open complaints. Click any number to jump to the filtered list.
- **Complaints**: search and filter by status, department, zone, ward, priority, SLA state and date; **Export CSV** downloads exactly the filtered rows.
- Open a complaint to:
  - **Assign officer / Reassign** — pick from the officer directory, which shows each officer's active and overdue load and how many jobs they already have within 2 km. Reassignment is possible only before work starts.
  - **Unassign** — returns the complaint to Pending (reason required).
  - **Priority / area** — set priority, department, zone and ward. Zones and wards are typed in; previously used values are suggested.
  - **Close** — for resolved complaints after verification.
  - See *Nearby complaints* within 500 m to spot duplicates.
- **City Map**: markers coloured by status; switch to *Clusters* or *Density* (heatmap) and filter.
- **Officers**: directory from the Cognito *Officer* group with workload, overdue, completed, average resolution time and SLA rate. Add or disable officers in Amazon Cognito (see `DEPLOYMENT_GUIDE.md` §8).
- **Departments & Areas**: department performance and a zone → ward breakdown.
- **Analytics & Reports**: pick a period and optional department/zone/ward; view trend (day/week/month), SLA compliance, resolution rate, category distribution, officer, department, zone and ward performance and a heatmap. Download a **Summary CSV** or the **Records CSV**. All numbers come from stored complaints.

## Notifications

Emails are sent when a complaint is registered, assigned, started, resolved, reopened, and for SLA warnings and breaches. While the AWS account's email service is in sandbox mode, only verified addresses receive mail; the complaint shows "Not delivered" in that case.
