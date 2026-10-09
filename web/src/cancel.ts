/** The parent backed out (closed the passkey prompt or the "Confirm it's you" dialog): not an error to show. */
export class UserCancelled extends Error {
  constructor(message = "Cancelled") {
    super(message);
  }
}
