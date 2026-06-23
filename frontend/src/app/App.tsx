import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { APIProvider } from "@vis.gl/react-google-maps";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConfirmProvider } from "@/components/ui/confirm-dialog";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { AuthProvider } from "@/lib/auth";
import { PushForegroundListener } from "@/lib/PushForegroundListener";
import { GOOGLE_MAPS_API_KEY, MAP_LIBRARIES } from "@/lib/googleMaps";
import { router } from "./routes";

const queryClient = new QueryClient();

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* Loads the Google Maps JS API once for the whole app. Props are read
          on first mount only, so this must live at the root (not in a route). */}
      <APIProvider apiKey={GOOGLE_MAPS_API_KEY} libraries={MAP_LIBRARIES}>
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
      </APIProvider>
    </QueryClientProvider>
  );
}
