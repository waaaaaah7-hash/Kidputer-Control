import type { EnrollmentCode } from "./api";
import { formatDateTime } from "./format";

/** Shown once after creating a device or reissuing its code: the code is not retrievable later. */
export default function EnrollmentInfo({ code, childHint }: { code: EnrollmentCode; childHint?: string }) {
  const command = `.\\Install.ps1 -ApiUrl ${window.location.origin} -EnrollmentCode ${code.enrollment_code} -ChildUser ${childHint || "<child's Windows account>"}`;
  return (
    <div className="enroll">
      <p>Enrollment code (valid until {formatDateTime(code.expires_at)}, single use):</p>
      <p className="enroll-code" data-testid="enrollment-code">
        {code.enrollment_code}
      </p>
      <p>
        On the child's PC, open <strong>PowerShell as administrator</strong> in the installer (or repository <code>agent</code>) folder
        and run:
      </p>
      <pre className="command">{command}</pre>
      <p className="muted small">This code won't be shown again. If it expires, get a new one from the device's page.</p>
    </div>
  );
}
