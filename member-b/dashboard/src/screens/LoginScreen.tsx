import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useShell } from "../shell/ShellContext";
import { bClient, APIClientError } from "../api/bClient";
import styles from "../styles/shell.module.css";

export const LoginScreen: React.FC = () => {
  const navigate = useNavigate();
  const { login } = useShell();

  const [username, setUsername] = useState("officer_01");
  const [password, setPassword] = useState("jal-demo-2026");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await bClient.login(username, password);
      login(res.token, res.officer_id, res.role);
      navigate("/snapshot", { replace: true });
    } catch (err) {
      if (err instanceof APIClientError) {
        setError(err.message || "Invalid credentials. Please check your username and password.");
      } else {
        setError("Network error connecting to authentication service.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.jpbLoginContainer} data-testid="login-screen">
      <div className={styles.jpbLoginCard}>
        <div style={{ textAlign: "center", marginBottom: "24px" }}>
          <h2 style={{ margin: "0 0 6px 0", color: "var(--jp-accent)" }}>JalPriority AI</h2>
          <span className={styles.jpbDisclaimerBadge}>
            SYNTHETIC DATA · SIMULATION RESULTS
          </span>
          <p style={{ color: "var(--jp-muted)", fontSize: "0.85rem", marginTop: "12px" }}>
            Municipal Water Distribution &amp; Fairness Orchestration
          </p>
        </div>

        {error && (
          <div className={styles.jpbErrorCallout} style={{ marginBottom: "16px" }} data-testid="login-error">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} data-testid="login-form">
          <div className={styles.jpbFormGroup}>
            <label htmlFor="username" className={styles.jpbLabel}>
              Username / Officer ID
            </label>
            <input
              id="username"
              type="text"
              className={styles.jpbInput}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              data-testid="username-input"
            />
          </div>

          <div className={styles.jpbFormGroup}>
            <label htmlFor="password" className={styles.jpbLabel}>
              Password
            </label>
            <input
              id="password"
              type="password"
              className={styles.jpbInput}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              data-testid="password-input"
            />
          </div>

          <button
            type="submit"
            className={`${styles.jpbBtn} ${styles.jpbBtnPrimary}`}
            style={{ width: "100%", padding: "10px", marginTop: "8px" }}
            disabled={loading}
            data-testid="login-submit-btn"
          >
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        <div style={{ marginTop: "20px", fontSize: "0.75rem", color: "var(--jp-muted)", textAlign: "center" }}>
          Demo Credentials: <code>officer_01 / jal-demo-2026</code>
        </div>
      </div>
    </div>
  );
};
