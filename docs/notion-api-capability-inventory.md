# Notion API Capability Inventory

This inventory tracks Capture to Notion Adapter coverage, raw API candidates that require verification, and deferred capability groups.

## Implemented Adapter capabilities

### Views

Implemented through `raw client.request()` calls:

- List views
- Retrieve view
- Create view
- Update view
- Delete view

### Blocks

- Retrieve block
- Update block
- Delete block
- List block children
- Append block children

### Pages

- Retrieve page
- Create page
- Update page
- Move page
- Retrieve page property

### Databases

- Retrieve database
- Create database
- Update database

### Data sources

- Retrieve data source
- Query data source
- Update data source
- Create data source
- List data source templates

### File uploads

- Create file upload
- Send file upload
- Complete file upload
- Retrieve file upload
- List file uploads

### Users

- List users
- Search users by email/name/display name through adapter filtering

## Raw API candidates requiring endpoint verification

Do not implement any raw API candidate until the exact path, HTTP method, request shape, response shape, and Notion API version behavior are verified.

Candidates:

- Retrieve page as markdown
- Update page content as markdown
- Create view query
- Get view query results
- Delete view query
- Update comment
- Delete comment
- List custom emojis

## Deferred SDK-backed capabilities

These capabilities are deferred and should use SDK-backed support where appropriate:

- Comments
  - Create comment
  - List comments
  - Retrieve comment
- Users
  - Retrieve bot user with `me`
  - Retrieve user
- OAuth
  - Create token
  - Revoke token
  - Introspect token

## Deferred event-driven capabilities

- Webhooks
- Webhook event handling
