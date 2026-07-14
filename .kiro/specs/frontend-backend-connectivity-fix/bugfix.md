# Bugfix Requirements Document

## Introduction

The Next.js frontend application (running on localhost:3000) cannot connect to the FastAPI backend (expected on localhost:8000), resulting in "Failed to fetch" errors for all API calls. This prevents the application from functioning as users cannot load datasets, fetch mosque data, or perform routing operations. The bug manifests in the `fetchMosques` function and all other API functions defined in `frontend/src/lib/api.ts`.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the frontend application calls any API endpoint (e.g., `fetchMosques`, `fetchDatasets`, `routeToMosque`) THEN the system throws a "Failed to fetch" TypeError

1.2 WHEN the frontend attempts to connect to `http://${window.location.hostname}:8000` THEN the network request fails with no response from the backend

1.3 WHEN the backend `main.py` file contains unresolved merge conflicts THEN the FastAPI application cannot start properly on port 8000

1.4 WHEN CORS is misconfigured or the backend is not running THEN cross-origin requests from localhost:3000 to localhost:8000 are blocked or fail

### Expected Behavior (Correct)

2.1 WHEN the frontend application calls any API endpoint THEN the system SHALL successfully fetch data from the backend and return the expected JSON response

2.2 WHEN the frontend attempts to connect to `http://${window.location.hostname}:8000` THEN the backend SHALL respond with appropriate data or error messages (not network-level failures)

2.3 WHEN the backend `main.py` file is properly configured THEN the FastAPI application SHALL start successfully on port 8000 with all routes accessible

2.4 WHEN CORS is properly configured THEN cross-origin requests from localhost:3000 SHALL be allowed and processed by the backend

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the API_BASE configuration uses server-side rendering context (`typeof window === "undefined"`) THEN the system SHALL CONTINUE TO use `http://127.0.0.1:8000` as the base URL

3.2 WHEN the API endpoints receive valid requests with proper parameters THEN the system SHALL CONTINUE TO return the same response format and data structure

3.3 WHEN CORS is configured with `allow_origins=["*"]` THEN the system SHALL CONTINUE TO accept requests from any origin for development purposes

3.4 WHEN the backend routes are registered under `/api/v1` prefix THEN the system SHALL CONTINUE TO respond to requests at paths like `/api/v1/datasets`, `/api/v1/mosques`, etc.

## Bug Condition

### Bug Condition Function

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type APIRequest
  OUTPUT: boolean
  
  // Returns true when API connectivity is broken
  RETURN (X.origin = "http://localhost:3000" OR X.origin = "http://127.0.0.1:3000") AND
         (X.targetURL starts with "http://localhost:8000" OR X.targetURL starts with "http://127.0.0.1:8000") AND
         (backend_not_running(8000) OR backend_has_startup_errors() OR cors_blocks_request(X))
END FUNCTION
```

### Property Specification

```pascal
// Property: Fix Checking - API Connectivity Works
FOR ALL X WHERE isBugCondition(X) DO
  result ← makeAPICall'(X)
  ASSERT result.status IN [200, 201, 400, 404, 422, 500] AND 
         result.type != "network_error" AND
         result.error_message != "Failed to fetch"
END FOR
```

**Key Definitions:**
- **F**: The original (unfixed) system - backend with merge conflicts, potentially not running
- **F'**: The fixed system - backend properly started on port 8000, CORS configured, merge conflicts resolved

### Preservation Goal

```pascal
// Property: Preservation Checking - Existing API Behavior Unchanged
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

This ensures that for API requests that were already working (if any), or for backend responses to valid requests, the behavior remains identical after the fix.

## Root Cause Analysis

The bug likely stems from one or more of the following issues:

1. **Merge Conflict**: The `backend/app/main.py` file contains unresolved Git merge conflicts that prevent the FastAPI application from starting
2. **Backend Not Running**: The backend service is not running on port 8000
3. **Port Mismatch**: The backend might be running on a different port than expected
4. **CORS Issues**: Although CORS is configured to allow all origins, startup errors might prevent proper initialization
5. **Firewall/Network**: Windows firewall or security software might be blocking localhost:8000 connections

## Counterexample

```typescript
// Current behavior (crashes/fails):
const response = await fetchMosques("dataset_id", 20, 0);
// Throws: TypeError: Failed to fetch

// Expected behavior (should work):
const response = await fetchMosques("dataset_id", 20, 0);
// Returns: { dataset_id: "...", total: 100, items: [...] }
```
