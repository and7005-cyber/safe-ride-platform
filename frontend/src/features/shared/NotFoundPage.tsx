import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="text-center space-y-4">
        <h1 className="font-heading text-5xl font-bold text-primary">404</h1>
        <p className="text-muted-foreground">This page could not be found.</p>
        <Button onClick={() => navigate("/")}>Return home</Button>
      </div>
    </div>
  );
}
