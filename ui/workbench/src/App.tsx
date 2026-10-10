import {
  AppShell, Badge, Button, Center, Group, Loader, Menu, Paper, Select, Stack, Text, ThemeIcon, Title, UnstyledButton,
} from "@mantine/core";
import {
  IconChecklist, IconChevronDown, IconListDetails, IconLogout, IconShieldCheck, IconUserCircle,
} from "@tabler/icons-react";
import { useEffect, useState, type ReactNode } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";

import { hasRole, useAuth } from "./auth";
import { CasePage } from "./pages/CasePage";
import { QAPage } from "./pages/QAPage";
import { QueuePage } from "./pages/QueuePage";

const DEMO_ROLES = ["l1", "l2", "mlro", "qa", "admin"] as const;
const ROLE_LABEL: Record<string, string> = {
  l1: "L1 analyst", l2: "L2 investigator", mlro: "MLRO", qa: "QA reviewer", sme: "SME", admin: "Administrator",
};

export function App() {
  const { loading, error, me } = useAuth();
  if (loading) return <Center h="100vh"><Loader /></Center>;
  if (error) return <Center h="100vh"><Text c="red">{error}</Text></Center>;
  return (
    <Routes>
      <Route path="/callback" element={<Callback />} />
      <Route path="*" element={me ? <Shell /> : <SignIn />} />
    </Routes>
  );
}

function Callback() {
  const { completeSignIn } = useAuth();
  const navigate = useNavigate();
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    completeSignIn().then(() => navigate("/", { replace: true })).catch((e) => setFailed(String(e)));
  }, [completeSignIn, navigate]);
  return <Center h="100vh">{failed ? <Text c="red">Sign-in failed: {failed}</Text> : <Loader />}</Center>;
}

function SignIn() {
  const { mode, signIn, demoRoleSwitch, switchRole } = useAuth();
  return (
    <Center h="100vh" className="sn-header">
      <Paper shadow="lg" p="xl" radius="lg" w={420}>
        <Stack gap="md">
          <Group gap="sm">
            <ThemeIcon size={44} radius="md" variant="light"><IconShieldCheck size={28} /></ThemeIcon>
            <div>
              <Title order={3}>Sentinel Workbench</Title>
              <Text size="sm" c="dimmed">AML investigation · agents recommend, investigators decide</Text>
            </div>
          </Group>
          {mode === "cognito" && <Button size="md" onClick={() => signIn()}>Sign in</Button>}
          {(mode !== "cognito" || demoRoleSwitch) && (
            <>
              <Text size="sm" c="dimmed">{mode === "cognito" ? "Demo: continue as a test user" : "Dev mode (no Cognito): choose a test user."}</Text>
              {DEMO_ROLES.map((r) => (
                <Button key={r} variant="light" onClick={() => (mode === "cognito" ? switchRole(r) : signIn(r))}>
                  Continue as {ROLE_LABEL[r]}
                </Button>
              ))}
            </>
          )}
          <Text size="xs" c="dimmed">Synthetic data only · proof of concept</Text>
        </Stack>
      </Paper>
    </Center>
  );
}

function Shell() {
  const { me, signOut, mode, demoRoleSwitch, switchRole } = useAuth();
  const navigate = useNavigate();
  const nav = (to: string, label: string, icon: ReactNode) => (
    <NavLink to={to} style={{ textDecoration: "none" }}>
      {({ isActive }) => (
        <Button variant={isActive ? "white" : "subtle"} color={isActive ? "brand" : "gray.0"} leftSection={icon}
                c={isActive ? undefined : "white"} size="sm">
          {label}
        </Button>
      )}
    </NavLink>
  );
  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header className="sn-header">
        <Group h="100%" px="lg" justify="space-between">
          <Group gap="xl">
            <Group gap={8}>
              <IconShieldCheck size={26} />
              <Text fw={700} size="lg">Sentinel</Text>
              <Text size="sm" opacity={0.75}>Investigator workbench</Text>
            </Group>
            <Group gap={4}>
              {nav("/", "Work queue", <IconListDetails size={16} />)}
              {hasRole(me, "qa", "admin") && nav("/qa", "QA review", <IconChecklist size={16} />)}
            </Group>
          </Group>
          <Group gap="md">
          {demoRoleSwitch && (
            <Select size="xs" w={190} aria-label="View as" allowDeselect={false} comboboxProps={{ withinPortal: true }}
                    value={me!.roles[0]} leftSection={<IconUserCircle size={16} />}
                    data={DEMO_ROLES.map((r) => ({ value: r, label: `View as ${ROLE_LABEL[r]}` }))}
                    onChange={(r) => r && switchRole(r).then(() => navigate("/"))} data-testid="role-switch" />
          )}
          <Menu position="bottom-end" width={260}>
            <Menu.Target>
              <UnstyledButton c="white">
                <Group gap={8}>
                  <div style={{ textAlign: "right" }}>
                    <Text size="sm" fw={600}>{me!.username}</Text>
                    <Text size="xs" opacity={0.8}>{me!.roles.map((r) => ROLE_LABEL[r] ?? r).join(", ")}</Text>
                  </div>
                  <IconChevronDown size={16} />
                </Group>
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>Signed in {mode === "cognito" ? "with Cognito" : "in dev mode"}</Menu.Label>
              <Menu.Item disabled>
                <Badge color={me!.sees_pii ? "teal" : "gray"} variant="light">
                  {me!.sees_pii ? "Sees customer data" : "Customer data masked"}
                </Badge>
              </Menu.Item>
              <Menu.Divider />
              <Menu.Item leftSection={<IconLogout size={16} />} onClick={() => signOut()}>
                Sign out / switch user
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Routes>
          <Route path="/" element={<QueuePage />} />
          <Route path="/cases/:caseId" element={<CasePage />} />
          <Route path="/qa" element={<QAPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
