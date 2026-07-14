# Frontend-Backend Connectivity Fix Design

## Overview

The Next.js frontend cannot connect to the FastAPI backend due to unresolved merge conflicts in `backend/app/main.py` that prevent the backend from starting on port 8000. The merge conflict shows two different versions of the code: a clean architecture version that uses `app.include_router(router, prefix="/api/v1")` and an older version with inline route definitions. This fix resolves the merge conflict by keeping the clean architecture approach, ensuring the backend starts successfully and responds to frontend API requests.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug - frontend API calls fail with "Failed to fetch" because the backend cannot start due to merge conflicts in main.py
- **Property (P)**: The desired behavior when frontend makes API calls - backend should respond with valid HTTP status codes (200, 404, 422, 500, etc.) instead of network-level failures
- **Preservation**: Existing API endpoint behavior, CORS configuration, and route registration under `/api/v1` prefix that must remain unchanged
- **main.py**: The FastAPI application entry point in `backend/app/main.py` that configures middleware, CORS, and route registration
- **router**: The APIRouter instance from `backend/app/interfaces/api/routes.py` that defines all API endpoints
- **API_BASE**: The frontend configuration in `frontend/src/lib/api.ts` that constructs the backend URL using `http://${window.location.hostname}:8000`

## Bug Details

### Bug Condition

The bug manifests when the frontend application attempts to make API calls to the backend. The `backend/app/main.py` file contains unresolved Git merge conflicts between two versions:
1. Clean architecture version with `app.include_router(router, prefix="/api/v1")`
2. Legacy version with inline route definitions (hundreds of lines)

These merge conflicts prevent the FastAPI application from starting, causing all frontend API requests to fail at the network level.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type APIRequest
  OUTPUT: boolean
  
  RETURN input.sourceOrigin IN ['http://localhost:3000', 'http://127.0.0.1:3000']
         AND input.targetURL starts with 'http://localhost:8000' OR starts with 'http://127.0.0.1:8000'
         AND (backend_not_running_on_port_8000() OR backend_startup_failed_due_to_merge_conflict())
         AND fetch_throws_network_error(input)
END FUNCTION
```

### Examples

- **Concrete example 1**: Calling `fetchMosques("dataset_id", 20, 0)` from frontend throws `TypeError: Failed to fetch` because backend is not running on port 8000
- **Concrete example 2**: Calling `fetchDatasets()` from frontend throws `TypeError: Failed to fetch` with no HTTP response
- **Concrete example 3**: Navigating to `http://localhost:8000/api/v1/health` in browser returns connection refused or no response
- **Edge case**: Even after resolving conflicts, if backend starts but CORS is misconfigured, requests would fail with CORS errors (different from "Failed to fetch")

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- All API endpoint paths must continue to be prefixed with `/api/v1` (e.g., `/api/v1/datasets`, `/api/v1/mosques`)
- CORS configuration must continue to allow all origins with `allow_origins=["*"]` for development
- API response formats and data structures must remain identical
- Server-side rendering context must continue to use `http://127.0.0.1:8000` as base URL

**Scope:**
All API requests that work correctly after the backend is running should behave identically. This includes:
- API endpoint responses (JSON structure, status codes)
- CORS headers in responses
- Route registration under `/api/v1` prefix
- Database initialization on startup

## Hypothesized Root Cause

Based on the bug description and examining `backend/app/main.py`, the root causes are:

1. **Merge Conflict in main.py**: The file contains unresolved Git merge conflict markers (`<<<<<<< HEAD`, `=======`, `>>>>>>> 096c8ae`) that prevent Python from parsing the file, causing FastAPI to crash on startup

2. **Two Competing Code Versions**:
   - **HEAD version** (correct): Uses clean architecture with `app.include_router(router, prefix="/api/v1")` - 13 lines total
   - **Incoming branch version** (legacy): Contains hundreds of lines of inline route definitions that duplicate functionality already in `routes.py`

3. **Backend Not Running**: Due to the merge conflict, running `uvicorn app.main:app` or any startup command fails with Python syntax errors, preventing the backend from binding to port 8000

4. **Frontend Cannot Connect**: With no server listening on port 8000, all frontend `fetch()` calls fail immediately with network-level "Failed to fetch" errors before any HTTP communication occurs

## Correctness Properties

Property 1: Bug Condition - API Connectivity Works

_For any_ API request from the frontend (localhost:3000) to backend endpoints (localhost:8000/api/v1/*), after resolving the merge conflict and starting the backend, the fixed system SHALL return valid HTTP responses with status codes in [200, 201, 400, 404, 422, 500] instead of network-level "Failed to fetch" errors.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Preservation - Existing API Behavior

_For any_ API endpoint that is correctly implemented in `routes.py`, the fixed code SHALL produce the same response format, data structure, and status codes as before the merge conflict, preserving all endpoint functionality under the `/api/v1` prefix.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct (merge conflict in main.py prevents backend startup):

**File**: `backend/app/main.py`

**Function**: N/A (file-level merge conflict resolution)

**Specific Changes**:
1. **Remove Merge Conflict Markers**: Delete all Git merge conflict markers (`<<<<<<< HEAD`, `=======`, `>>>>>>> 096c8ae`)

2. **Keep Clean Architecture Version (HEAD)**: Retain the clean architecture implementation that uses:
   - `from app.interfaces.api.routes import router`
   - `app.include_router(router, prefix="/api/v1")`
   - Minimal main.py with proper separation of concerns

3. **Discard Legacy Inline Routes**: Remove the 500+ lines of inline route definitions between `=======` and `>>>>>>>` markers since they duplicate functionality in `routes.py`

4. **Verify Route Registration**: Ensure `app.include_router(router, prefix="/api/v1")` is the single line that registers all routes from `routes.py`

5. **Preserve CORS and Middleware**: Keep the existing CORS configuration with `allow_origins=["*"]` and GZipMiddleware settings

**Expected main.py after fix** (approximately):
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.interfaces.api.routes import router
from app.infrastructure.database.arangodb_client import init_db

app = FastAPI(
    title="iMosque ArangoDB Web API (Clean Architecture)",
    description="...",
    version="4.0.0",
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    init_db()

app.include_router(router, prefix="/api/v1")
```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, verify the bug exists on the unfixed code (backend won't start), then verify the fix resolves connectivity and preserves all API functionality.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm that the merge conflict prevents backend startup and causes "Failed to fetch" errors.

**Test Plan**: Attempt to start the backend with the unfixed `main.py` file and observe the startup failure. Then attempt frontend API calls to confirm they fail with network errors.

**Test Cases**:
1. **Backend Startup Test**: Run `uvicorn app.main:app` from the backend directory (will fail on unfixed code with Python syntax error due to merge conflict)
2. **Frontend Fetch Test**: With backend not running, call `fetchMosques()` from frontend (will fail with "Failed to fetch" on unfixed code)
3. **Health Check Test**: Navigate to `http://localhost:8000/api/v1/health` in browser (will fail with connection refused on unfixed code)
4. **Port Check Test**: Verify nothing is listening on port 8000 using `netstat -ano | findstr :8000` on Windows (will show no process on unfixed code)

**Expected Counterexamples**:
- Backend startup fails with `SyntaxError: invalid syntax` or similar Python parsing error
- Frontend API calls throw `TypeError: Failed to fetch`
- Browser shows "This site can't be reached" or "Connection refused" for localhost:8000
- Port 8000 has no listening process

### Fix Checking

**Goal**: Verify that for all frontend API requests (where the bug condition holds), the fixed system produces valid HTTP responses instead of network errors.

**Pseudocode:**
```
FOR ALL request WHERE isBugCondition(request) DO
  result := makeAPIRequest_fixed(request)
  ASSERT result.statusCode IN [200, 201, 400, 404, 422, 500]
  ASSERT result.errorType != "network_error"
  ASSERT result.errorMessage != "Failed to fetch"
END FOR
```

### Preservation Checking

**Goal**: Verify that for all API endpoints that were working (or would work with a running backend), the fixed code produces identical responses.

**Pseudocode:**
```
FOR ALL endpoint IN expected_api_endpoints DO
  ASSERT endpoint.path starts with "/api/v1/"
  ASSERT endpoint.corsHeaders include "access-control-allow-origin: *"
  ASSERT endpoint.responseFormat matches schema_from_routes_py
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across different API endpoints
- It catches edge cases in request parameters that manual unit tests might miss
- It provides strong guarantees that API behavior is unchanged for all valid requests

**Test Plan**: After resolving the merge conflict, start the backend and observe that all API endpoints respond correctly. Then write property-based tests capturing expected response formats.

**Test Cases**:
1. **API Endpoint Preservation**: Verify all endpoints in `routes.py` are accessible under `/api/v1/` prefix (e.g., `/api/v1/health`, `/api/v1/datasets`, `/api/v1/mosques`)
2. **CORS Headers Preservation**: Verify CORS headers are present in all API responses with `allow_origins=["*"]`
3. **Response Format Preservation**: Verify API responses match expected JSON schemas (e.g., `fetchMosques` returns `{dataset_id, total, limit, offset, items}`)
4. **Server-Side Rendering Preservation**: Verify that during SSR, API_BASE uses `http://127.0.0.1:8000`

### Unit Tests

- Test backend starts successfully after merge conflict resolution
- Test `/api/v1/health` endpoint returns 200 status
- Test `/api/v1/datasets` endpoint returns valid JSON
- Test frontend `fetchMosques()` successfully retrieves data without network errors
- Test CORS headers are present in responses

### Property-Based Tests

- Generate random valid API requests and verify all return proper HTTP status codes (not network errors)
- Generate random dataset IDs and verify `fetchMosques()` returns properly formatted responses
- Test that all endpoints under `/api/v1/` are accessible and return valid responses
- Verify CORS headers are present across all API responses

### Integration Tests

- Start backend, call all frontend API functions, verify no "Failed to fetch" errors
- Test full flow: upload dataset → fetch datasets → fetch mosques → route to mosque
- Verify frontend can connect to backend from localhost:3000 to localhost:8000
- Test that backend continues running without crashes after handling multiple requests
