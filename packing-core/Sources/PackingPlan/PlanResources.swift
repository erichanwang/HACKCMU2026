import Foundation

/// The bundle holding this module's own resources — `plan.json`, `container.json`,
/// `scanned-item.json`, `nested-plan.json`.
///
/// Tests must ask for it by name rather than writing `.module`. `Bundle.module`
/// is generated per target, so once the test target gained resources of its own
/// its `Bundle.module` shadowed this one at every call site inside the tests, and
/// library fixtures stopped being found. Naming the bundle removes the ambiguity.
public enum PlanResources {
    public static let bundle = Bundle.module
}
