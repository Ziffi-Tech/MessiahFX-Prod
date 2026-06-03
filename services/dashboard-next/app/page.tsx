import { redirect } from "next/navigation";

// Root → redirect to the trading dashboard
// The middleware handles auth, so unauthenticated users go to /login
export default function RootPage() {
  redirect("/");
}
