import { createFileRoute } from "@tanstack/react-router";
import { Workspace } from "@/components/dialogspan/workspace";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  return <Workspace />;
}
