import { fireEvent, screen, waitFor } from "@testing-library/react";

import { jsonResponse, makeMe, mockApi, renderApp, signedInRoutes } from "./helpers";

const signedOut = {
  "POST /auth/refresh": jsonResponse({ detail: "Not signed in" }, 401),
};

describe("Authentication", () => {
  it("sends signed-out visitors to the login page", async () => {
    mockApi(signedOut);
    renderApp("/users");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("signs in and shows the dashboard with the user's organization", async () => {
    const fetchSpy = mockApi({
      ...signedOut,
      ...Object.fromEntries(Object.entries(signedInRoutes()).filter(([k]) => k !== "POST /auth/refresh")),
      "POST /auth/login": jsonResponse({ access_token: "token-1", token_type: "bearer", expires_in: 900 }),
    });
    renderApp("/");

    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "asha@acme.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "Correct-Horse-Battery-9" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("System overview")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("Asha Admin")).toBeInTheDocument();

    // The access token is sent as a Bearer header on API calls.
    const meCall = fetchSpy.mock.calls.find(([url]) => String(url).endsWith("/auth/me"));
    expect(new Headers(meCall?.[1]?.headers).get("Authorization")).toBe("Bearer token-1");
  });

  it("shows the server's error message on a failed login", async () => {
    mockApi({
      ...signedOut,
      "POST /auth/login": jsonResponse({ detail: "Invalid email or password" }, 401),
    });
    renderApp("/login");

    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "asha@acme.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });

  it("resumes an existing session from the refresh cookie", async () => {
    mockApi(signedInRoutes());
    renderApp("/");
    expect(await screen.findByText("System overview")).toBeInTheDocument();
  });

  it("refreshes the access token once when it expires, then retries", async () => {
    let refreshCalls = 0;
    let usersCalls = 0;
    mockApi({
      ...signedInRoutes(),
      "POST /auth/refresh": () => {
        refreshCalls += 1;
        return jsonResponse({ access_token: `token-${refreshCalls}`, token_type: "bearer", expires_in: 900 });
      },
      "GET /users": () => {
        usersCalls += 1;
        // First call: access token expired. Second call (after refresh): success.
        return usersCalls === 1
          ? jsonResponse({ detail: "Not authenticated" }, 401)
          : jsonResponse([makeMe().user]);
      },
    });
    renderApp("/users");

    expect(await screen.findByText("asha@acme.com")).toBeInTheDocument();
    expect(refreshCalls).toBe(2); // once on page load, once after the 401
    expect(usersCalls).toBe(2);
  });

  it("signs out", async () => {
    mockApi({ ...signedInRoutes(), "POST /auth/logout": jsonResponse(null, 204) });
    renderApp("/");
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument());
  });
});
