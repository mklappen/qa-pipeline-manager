# System Use Case Specifications (TSM)

## 1. [UC - TSM] - User Login

### USE CASE: User Login
- **Description:** Authenticated user accesses the system using email and password
- **Actors:** User, Authentication Service
- **Pre-conditions:** User has a registered account with verified email
- **Main Success Scenario Steps:**
  1. User navigates to the login page
  2. User enters valid email and password
  3. System validates credentials against the database
  4. System creates a session and redirects user to dashboard
- **Alternative Scenario Steps (Negative Path):**
  1. User enters invalid credentials
  2. System displays "Invalid email or password" error
  3. Failed attempt counter is incremented
- **Exception Scenario Steps (Edge Case):**
  1. Authentication service is unavailable
  2. System displays a maintenance message and logs the incident

---

## 2. [UC - TSM] - Password Reset

### USE CASE: Password Reset
- **Description:** User resets forgotten password via email verification
- **Actors:** User, Email Service
- **Pre-conditions:** User has a registered and verified email address
- **Main Success Scenario Steps:**
  1. User clicks "Forgot Password" on the login page
  2. User enters their registered email address
  3. System sends a password reset link valid for 1 hour
  4. User clicks the link and sets a new password
  5. System confirms the change and redirects to login
- **Alternative Scenario Steps (Negative Path):**
  1. Email address is not found in the system
  2. System displays a neutral message (does not confirm/deny existence)
- **Exception Scenario Steps (Edge Case):**
  1. Email service is unavailable
  2. System queues the email for retry and notifies user of delay

---
