// Exercises the pure helpers in Spike/API.swift (URL resolution, error-message mapping) compiled
// with -DPACKAR_TEST_ONLY, which strips the PackingPlan/UIKit-dependent `enum API` out of that file
// so it builds on Linux. Plain asserts; run.sh treats any trap/crash as failure.
import Foundation

var failures = 0
func check(_ name: String, _ got: String, _ want: String) {
    if got != want {
        print("FAIL \(name): got \(got.debugDescription) want \(want.debugDescription)")
        failures += 1
    }
}

// resolveServerURL: happy path, no scheme typed, blank, malformed, whitespace-only all fall back safely.
check("typed with scheme", resolveServerURL(typed: "http://10.0.0.5:8000", fallback: "http://fallback:1").absoluteString, "http://10.0.0.5:8000")
check("typed bare host:port", resolveServerURL(typed: "10.0.0.5:8000", fallback: "http://fallback:1").absoluteString, "http://10.0.0.5:8000")
check("blank falls back", resolveServerURL(typed: "", fallback: "http://fallback:1").absoluteString, "http://fallback:1")
check("whitespace-only falls back", resolveServerURL(typed: "   ", fallback: "http://fallback:1").absoluteString, "http://fallback:1")
check("scheme-only, no host falls back", resolveServerURL(typed: "http://", fallback: "http://fallback:1").absoluteString, "http://fallback:1")
check("spaces unparseable falls back", resolveServerURL(typed: "not a url", fallback: "http://fallback:1").absoluteString, "http://fallback:1")
check("bad fallback still doesn't crash", resolveServerURL(typed: "", fallback: "").absoluteString, "http://172.26.48.172:8000")

// serverErrorMessage: {"detail": ...} body, non-JSON text body, empty body.
check("detail JSON", serverErrorMessage(status: 409, body: Data(#"{"detail": "suitcase has no scanned items to pack"}"#.utf8)), "suitcase has no scanned items to pack")
check("non-JSON text body", serverErrorMessage(status: 502, body: Data("Bad Gateway".utf8)), "Bad Gateway")
check("empty body", serverErrorMessage(status: 500, body: Data()), "server error (500)")
check("valid JSON, wrong shape", serverErrorMessage(status: 422, body: Data("[1,2,3]".utf8)), "[1,2,3]")

// networkErrorMessage: connection refused, DNS failure, and timeout each read distinctly.
check("connection refused", networkErrorMessage(.cannotConnectToHost), "can't reach the server — is it running?")
check("dns failure", networkErrorMessage(.cannotFindHost), "can't find that server — check the address in Settings")
check("timeout", networkErrorMessage(.timedOut), "server took too long to respond")
check("no internet", networkErrorMessage(.notConnectedToInternet), "phone isn't on a network")
if networkErrorMessage(.cannotConnectToHost) == networkErrorMessage(.cannotFindHost) {
    print("FAIL: connection-refused and dns-failure must read differently"); failures += 1
}

if failures == 0 {
    print("API helpers: \(15) checks passed")
} else {
    print("\(failures) failure(s)")
    exit(1)
}
