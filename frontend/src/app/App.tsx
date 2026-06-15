import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConfirmProvider } from "@/components/ui/confirm-dialog";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { AuthProvider } from "@/lib/auth";
import { PushForegroundListener } from "@/lib/PushForegroundListener";
import { router } from "./routes";

const queryClient = new QueryClient();

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <TooltipProvider>
          <ConfirmProvider>
            <RouterProvider router={router} />
            <Toaster />
            <Sonner />
            <PushForegroundListener />
          </ConfirmProvider>
        </TooltipProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
