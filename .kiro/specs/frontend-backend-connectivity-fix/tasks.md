# Implementation Plan

## Overview

This task list follows the exploratory bugfix workflow to resolve frontend-backend connectivity issues caused by merge conflicts in `backend/app/main.py`. The workflow follows these phases:
1. **Explore** - Write tests BEFORE fix to understand the bug (Bug Condition)
2. **Preserve** - Write tests for non-buggy behavior (Preservation Requirements)
3. **Implement** - Apply the fix with understanding (Expected Behavior)
4. **Validate** - Verify fix works and doesn't break anything

## Task Dependency Graph

```json
{
  "waves": [
    {
      "name": "Phase 1: Exploration",
      "taskIds": ["1"]
    },
    {
      "name": "Phase 2: Preservation",
      "taskIds": ["2"]
    },
    {
      "name": "Phase 3: Implementation",
      "taskIds": ["3"]
    },
    {
      "name": "Phase 4: Validation",
      "taskIds": ["4"]
    }
  ]
}
```

## Tasks

- [ ] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Backend Startup Fails and API Connectivity Broken
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: For this deterministic bug, scope the property to concrete failing cases:
    - Case 1: Backend startup fails with merge conflict syntax error
    - Case 2: Frontend API call to any endpoint throws "Failed to fetch"
    - Case 3: Health check endpoint is unreachable at localhost:8000
  - Test implementation details from Bug Condition in design:
    - Verify backend cannot start due to Python syntax error in main.py
    - Verify `fetch("http://localhost:8000/api/v1/health")` throws network error
    - Verify port 8000 has no listening process
    - Verify merge conflict markers exist in backend/app/main.py
  - The test assertions should match the Expected Behavior Properties from design:
    - After fix, backend SHALL start successfully
    - After fix, API calls SHALL return HTTP status codes (200, 404, etc.) not network errors
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples found:
    - "Backend startup fails with SyntaxError due to merge conflict markers"
    - "fetch() throws TypeError: Failed to fetch for all API endpoints"
    - "No process listening on port 8000"
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - API Endpoint Behavior Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Since backend cannot currently start due to merge conflict, we'll document expected preserved behaviors from `routes.py`:
    - Observe: All endpoints in routes.py are registered under `/api/v1/` prefix
    - Observe: CORS is configured with `allow_origins=["*"]`
    - Observe: Health endpoint should return `{"status": "healthy", "version": "1.0.0", ...}`
    - Observe: Datasets endpoint should return `{"active_dataset_id": ..., "items": [...]}`
  - Write property-based tests capturing these observed behavior patterns from Preservation Requirements:
    - Property: All API endpoints accessible under `/api/v1/` prefix return valid HTTP responses
    - Property: All API responses include CORS headers `access-control-allow-origin: *`
    - Property: API response formats match schemas in routes.py (e.g., fetchMosques returns {dataset_id, total, items})
    - Property: Server-side context uses `http://127.0.0.1:8000` for API_BASE
  - Property-based testing generates many test cases for stronger guarantees
  - These tests will be run AFTER the fix to verify preservation
  - Mark task complete when tests are written
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 3. Fix merge conflict in backend/app/main.py

  - [ ] 3.1 Resolve merge conflict in main.py
    - Open `backend/app/main.py`
    - Locate merge conflict markers: `<<<<<<< HEAD`, `=======`, `>>>>>>> 096c8ae6ace9c26a27b3adf04c8b2efbc3694a5a`
    - Keep the HEAD version (clean architecture with `app.include_router(router, prefix="/api/v1")`)
    - Delete all content between `=======` and `>>>>>>> 096c8ae` (legacy inline routes)
    - Delete all merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
    - Verify final file contains:
      - Import statements for FastAPI, middleware, router, init_db
      - FastAPI app creation with title, description, version
      - GZipMiddleware and CORSMiddleware configuration
      - `@app.on_event("startup")` with `init_db()` call
      - Single line: `app.include_router(router, prefix="/api/v1")`
    - _Bug_Condition: isBugCondition(input) where merge conflict prevents backend startup_
    - _Expected_Behavior: Backend starts successfully and responds to API requests with valid HTTP status codes_
    - _Preservation: API endpoints remain under /api/v1 prefix, CORS config unchanged, route registration preserved_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4_

  - [ ] 3.2 Verify backend starts successfully
    - Navigate to backend directory
    - Install dependencies if needed: `pip install -r requirements.txt` (if requirements.txt exists)
    - Start backend with: `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
    - Verify no Python syntax errors occur
    - Verify backend logs show "Application startup complete"
    - Verify backend is listening on port 8000
    - Keep backend running for next test step

  - [ ] 3.3 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Backend Starts and API Connectivity Works
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1:
      - Backend starts without syntax errors
      - fetch("http://localhost:8000/api/v1/health") returns HTTP 200
      - Port 8000 has a listening process
      - No merge conflict markers in main.py
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: Expected Behavior Properties from design - 2.1, 2.2, 2.3, 2.4_

  - [ ] 3.4 Verify preservation tests still pass
    - **Property 2: Preservation** - API Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2:
      - All endpoints under `/api/v1/` are accessible
      - CORS headers present in all responses
      - Response formats match expected schemas
      - SSR context uses correct API_BASE
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)

- [ ] 4. Checkpoint - Ensure all tests pass
  - Verify backend is running successfully on port 8000
  - Verify all bug condition tests pass (no "Failed to fetch" errors)
  - Verify all preservation tests pass (API behavior unchanged)
  - Test frontend connectivity by opening the frontend app and verifying API calls work
  - If any issues arise, investigate and ask the user for guidance

## Notes

**Key Concepts:**
- **Bug Condition (C)**: Merge conflict in main.py prevents backend startup, causing "Failed to fetch" errors
- **Property (P)**: Backend starts successfully and responds with valid HTTP status codes
- **Preservation (¬C)**: API endpoints under `/api/v1`, CORS config, and response formats remain unchanged

**Testing Approach:**
1. Write exploration tests BEFORE implementing fix (tests will fail - this confirms bug exists)
2. Write preservation tests to capture expected behavior to preserve
3. Implement the fix by resolving merge conflicts
4. Verify exploration tests now pass (confirms bug is fixed)
5. Verify preservation tests still pass (confirms no regressions)

**Important Reminders:**
- Run tests on UNFIXED code first to understand the bug
- Document counterexamples when exploration tests fail
- Follow observation-first methodology for preservation tests
- Do NOT write new tests in verification steps - re-run existing tests
