import { Github as GitHubLogoIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";
import { GITHUB_URL } from "@/core/config";

import { Section } from "../section";

export function CommunitySection() {
  return (
    <Section
      title={
        <AuroraText colors={["#60A5FA", "#A5FA60", "#A560FA"]}>
          Join the Community
        </AuroraText>
      }
      subtitle="Contribute brilliant ideas to shape the future of Echo. Collaborate, innovate, and make impacts."
    >
      <div className="flex justify-center">
        <Button className="text-xl" size="lg" asChild>
          <Link to={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <GitHubLogoIcon />
            Contribute Now
          </Link>
        </Button>
      </div>
    </Section>
  );
}
