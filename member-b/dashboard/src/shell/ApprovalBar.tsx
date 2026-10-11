import React, { useState } from "react";
import { PlanRecord, Weights } from "../contract/types";
import { useShell } from "./ShellContext";
import { bClient, APIClientError } from "../api/bClient";
import styles from "../styles/shell.module.css";

interface ApprovalBarProps {
  plan: PlanRecord | null;
  onDecisionSuccess?: (plan: PlanRecord) => void;
}

export const ApprovalBar: React.FC<ApprovalBarProps> = ({ plan, onDecisionSuccess }) => {
  const { token, updatePlan } = useShell();

  const [confirmApproveOpen, setConfirmApproveOpen] = useState(false);
  const [editPopoverOpen, setEditPopoverOpen] = useState(false);
  const [rejectModalOpen, setRejectModalOpen] = useState(false);
  const [rejectNote, setRejectNote] = useState("");
  const [rejectError, setRejectError] = useState<string | null>(null);

  // Edit weights state (defaults or current plan weights)
  const [revisedWeights, setRevisedWeights] = useState<Weights>(() => {
    return (
      plan?.result?.weights || {
        urgency: 1.0,
        debt: 1.0,
        vulnerability: 0.5,
        travel: 20.0,
      }
    );
  });

  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  if (!plan) {
    return (
      <div className={styles.jpbApprovalBar}>
        <div className={styles.jpbApprovalInfo}>
          <span className={`${styles.jpbStatusBadge} ${styles.jpbStatusSuperseded}`}>
            No Plan Generated
          </span>
        </div>
      </div>
    );
  }

  const isRecommended = plan.status === "recommended";

  const getStatusBadgeClass = () => {
    switch (plan.status) {
      case "recommended":
        return styles.jpbStatusRecommended;
      case "approved":
        return styles.jpbStatusApproved;
      case "edited":
        return styles.jpbStatusEdited;
      case "rejected":
        return styles.jpbStatusRejected;
      case "superseded":
      default:
        return styles.jpbStatusSuperseded;
    }
  };

  const handleApprove = async () => {
    if (!token || !plan) return;
    setSubmitting(true);
    setApiError(null);
    try {
      const res = await bClient.recordDecision(token, plan.plan_id, "approved", null);
      const nextPlan = res.next_plan || res.plan;
      updatePlan(nextPlan);
      if (onDecisionSuccess) onDecisionSuccess(nextPlan);
      setConfirmApproveOpen(false);
    } catch (err) {
      if (err instanceof APIClientError) {
        setApiError(err.message);
      } else {
        setApiError("Failed to approve plan");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleEditWeights = async () => {
    if (!token || !plan) return;
    setSubmitting(true);
    setApiError(null);
    try {
      const res = await bClient.recordDecision(
        token,
        plan.plan_id,
        "edited",
        null,
        revisedWeights
      );
      const nextPlan = res.next_plan || res.plan;
      updatePlan(nextPlan);
      if (onDecisionSuccess) onDecisionSuccess(nextPlan);
      setEditPopoverOpen(false);
    } catch (err) {
      if (err instanceof APIClientError) {
        setApiError(err.message);
      } else {
        setApiError("Failed to edit weights and regenerate plan");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    if (!rejectNote.trim()) {
      setRejectError("A rejection note is required.");
      return;
    }
    if (!token || !plan) return;

    setSubmitting(true);
    setApiError(null);
    setRejectError(null);
    try {
      const res = await bClient.recordDecision(
        token,
        plan.plan_id,
        "rejected",
        rejectNote.trim()
      );
      const nextPlan = res.next_plan || res.plan;
      updatePlan(nextPlan);
      if (onDecisionSuccess) onDecisionSuccess(nextPlan);
      setRejectModalOpen(false);
      setRejectNote("");
    } catch (err) {
      if (err instanceof APIClientError) {
        setApiError(err.message);
      } else {
        setApiError("Failed to reject plan");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.jpbApprovalBar}>
      <div className={styles.jpbApprovalInfo}>
        <span className={`${styles.jpbStatusBadge} ${getStatusBadgeClass()}`} data-testid="plan-status">
          {plan.status}
        </span>
        <span style={{ fontSize: "0.85rem", color: "var(--jp-muted)" }}>
          Plan ID: <strong style={{ fontFamily: "var(--jp-mono)", color: "var(--jp-text)" }}>{plan.plan_id}</strong>
        </span>
        {plan.decision && (
          <span style={{ fontSize: "0.8rem", color: "var(--jp-muted)" }}>
            Decided by <strong>{plan.decision.officer_id}</strong> at{" "}
            {new Date(plan.decision.decided_at).toLocaleTimeString()}{" "}
            {plan.decision.note ? `("${plan.decision.note}")` : ""}
          </span>
        )}
      </div>

      <div className={styles.jpbApprovalActions}>
        {apiError && <div className={styles.jpbErrorCallout}>{apiError}</div>}

        <button
          type="button"
          className={`${styles.jpbBtn} ${styles.jpbBtnSuccess}`}
          disabled={!isRecommended || submitting}
          onClick={() => setConfirmApproveOpen(true)}
          data-testid="approve-btn"
        >
          Approve
        </button>

        <button
          type="button"
          className={`${styles.jpbBtn} ${styles.jpbBtnPrimary}`}
          disabled={!isRecommended || submitting}
          onClick={() => {
            setRevisedWeights(
              plan.result?.weights || {
                urgency: 1.0,
                debt: 1.0,
                vulnerability: 0.5,
                travel: 20.0,
              }
            );
            setEditPopoverOpen(!editPopoverOpen);
          }}
          data-testid="edit-weights-btn"
        >
          Edit Weights
        </button>

        <button
          type="button"
          className={`${styles.jpbBtn} ${styles.jpbBtnDanger}`}
          disabled={!isRecommended || submitting}
          onClick={() => {
            setRejectNote("");
            setRejectError(null);
            setRejectModalOpen(true);
          }}
          data-testid="reject-btn"
        >
          Reject
        </button>

        {/* Edit Weights Popover */}
        {editPopoverOpen && (
          <div className={styles.jpbPopover} data-testid="edit-weights-popover">
            <h4 style={{ margin: "0 0 12px 0", fontSize: "0.95rem" }}>Adjust Fairness Weights</h4>

            <div className={styles.jpbSliderGroup}>
              <div className={styles.jpbSliderLabel}>
                <span>Urgency (α)</span>
                <span>{revisedWeights.urgency.toFixed(1)}</span>
              </div>
              <input
                type="range"
                className={styles.jpbSlider}
                min="0.5"
                max="3.0"
                step="0.1"
                value={revisedWeights.urgency}
                onChange={(e) =>
                  setRevisedWeights({ ...revisedWeights, urgency: parseFloat(e.target.value) })
                }
              />
            </div>

            <div className={styles.jpbSliderGroup}>
              <div className={styles.jpbSliderLabel}>
                <span>Debt (β)</span>
                <span>{revisedWeights.debt.toFixed(1)}</span>
              </div>
              <input
                type="range"
                className={styles.jpbSlider}
                min="0.5"
                max="3.0"
                step="0.1"
                value={revisedWeights.debt}
                onChange={(e) =>
                  setRevisedWeights({ ...revisedWeights, debt: parseFloat(e.target.value) })
                }
              />
            </div>

            <div className={styles.jpbSliderGroup}>
              <div className={styles.jpbSliderLabel}>
                <span>Vulnerability (γ)</span>
                <span>{revisedWeights.vulnerability.toFixed(1)}</span>
              </div>
              <input
                type="range"
                className={styles.jpbSlider}
                min="0.0"
                max="2.0"
                step="0.1"
                value={revisedWeights.vulnerability}
                onChange={(e) =>
                  setRevisedWeights({ ...revisedWeights, vulnerability: parseFloat(e.target.value) })
                }
              />
            </div>

            <div className={styles.jpbSliderGroup}>
              <div className={styles.jpbSliderLabel}>
                <span>Travel Penalty (δ)</span>
                <span>{revisedWeights.travel.toFixed(0)}</span>
              </div>
              <input
                type="range"
                className={styles.jpbSlider}
                min="1.0"
                max="50.0"
                step="1.0"
                value={revisedWeights.travel}
                onChange={(e) =>
                  setRevisedWeights({ ...revisedWeights, travel: parseFloat(e.target.value) })
                }
              />
            </div>

            <div style={{ display: "flex", gap: "8px", marginTop: "16px", justifyContent: "flex-end" }}>
              <button
                type="button"
                className={`${styles.jpbBtn} ${styles.jpbBtnOutline}`}
                onClick={() => setEditPopoverOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className={`${styles.jpbBtn} ${styles.jpbBtnPrimary}`}
                disabled={submitting}
                onClick={handleEditWeights}
              >
                {submitting ? "Regenerating..." : "Regenerate Plan"}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Confirmation Dialog for Approval */}
      {confirmApproveOpen && (
        <div className={styles.jpbModalOverlay} data-testid="approve-modal">
          <div className={styles.jpbModal}>
            <h3 style={{ margin: "0 0 12px 0" }}>Confirm Plan Approval</h3>
            <p style={{ color: "var(--jp-muted)", fontSize: "0.9rem", lineHeight: 1.5 }}>
              Are you sure you want to approve plan <strong>{plan.plan_id}</strong> for immediate dispatch?
              This will update the deprivation debt ledger for window {plan.window_index}.
            </p>
            {apiError && <div className={styles.jpbErrorCallout}>{apiError}</div>}
            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end", marginTop: "20px" }}>
              <button
                type="button"
                className={`${styles.jpbBtn} ${styles.jpbBtnOutline}`}
                onClick={() => setConfirmApproveOpen(false)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className={`${styles.jpbBtn} ${styles.jpbBtnSuccess}`}
                onClick={handleApprove}
                disabled={submitting}
                data-testid="confirm-approve-btn"
              >
                {submitting ? "Approving..." : "Yes, Approve Plan"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rejection Modal */}
      {rejectModalOpen && (
        <div className={styles.jpbModalOverlay} data-testid="reject-modal">
          <div className={styles.jpbModal}>
            <h3 style={{ margin: "0 0 12px 0" }}>Reject Plan</h3>
            <p style={{ color: "var(--jp-muted)", fontSize: "0.85rem" }}>
              Please provide a note explaining the reason for rejecting plan <strong>{plan.plan_id}</strong>:
            </p>
            <textarea
              className={styles.jpbTextarea}
              placeholder="Enter rejection reason (required)..."
              value={rejectNote}
              onChange={(e) => {
                setRejectNote(e.target.value);
                if (rejectError) setRejectError(null);
              }}
              data-testid="reject-note-input"
            />
            {rejectError && <div className={styles.jpbErrorCallout}>{rejectError}</div>}
            {apiError && <div className={styles.jpbErrorCallout}>{apiError}</div>}
            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end", marginTop: "16px" }}>
              <button
                type="button"
                className={`${styles.jpbBtn} ${styles.jpbBtnOutline}`}
                onClick={() => setRejectModalOpen(false)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className={`${styles.jpbBtn} ${styles.jpbBtnDanger}`}
                onClick={handleReject}
                disabled={submitting}
                data-testid="confirm-reject-btn"
              >
                {submitting ? "Rejecting..." : "Confirm Rejection"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
