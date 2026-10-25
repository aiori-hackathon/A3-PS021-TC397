import { createFileRoute } from "@tanstack/react-router";
import { MethodPage } from "@/components/dialogspan/method-page";

export const Route = createFileRoute("/method")({
  component: MethodPage,
});
