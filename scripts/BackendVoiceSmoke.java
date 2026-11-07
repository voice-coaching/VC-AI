import java.nio.file.Files;
import java.nio.file.Path;
import org.example.voice.analysis.infrastructure.runpod.RunPodContract;
import org.example.voice.analysis.infrastructure.canonical.CanonicalCallbackDocument;
import org.example.voice.analysis.infrastructure.canonical.CanonicalHandoffDocument;
import org.example.voice.analysis.infrastructure.canonical.CanonicalPublicProjection;

/** Existing Backend parser/public projection, no application boot or DB writes. */
public class BackendVoiceSmoke {
    public static void main(String[] args) throws Exception {
        var contract = new RunPodContract();
        var result = CanonicalCallbackDocument.parse(Files.readAllBytes(Path.of(args[0])), contract);
        var handoff = CanonicalHandoffDocument.parse(Files.readAllBytes(Path.of(args[1])), contract);
        if (!"COMPLETED".equals(result.status().name()) || result.failure() != null
                || result.feedback() == null || result.feedback().isBlank()
                || !handoff.projection().identity().equals(result.identity())) {
            throw new IllegalStateException("BACKEND_VOICE_RESULT_NOT_COMPLETED");
        }
        var view = new CanonicalPublicProjection(contract).project(result);
        if (view.coaching() == null) throw new IllegalStateException("BACKEND_VOICE_FEEDBACK_MISSING");
        if (result.overallScore() == null || view.score() == null
                || view.score().overallScore() == null
                || view.score().overallScore().compareTo(result.overallScore()) != 0
                || !"RUBRIC_COMPUTED".equals(view.score().validity())) {
            throw new IllegalStateException("BACKEND_VOICE_NUMERIC_SCORE_MISSING_OR_CHANGED");
        }
        var sourceDetails = contract.parse(result.bytes(), "result").path("score").path("details");
        if (!sourceDetails.isMissingNode()) {
            var mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            if (view.score().details() == null || !contract.digest(sourceDetails).equals(
                    contract.digest(mapper.valueToTree(view.score().details())))) {
                throw new IllegalStateException("BACKEND_SCORE_DETAILS_DROPPED_OR_CHANGED");
            }
        }
        System.out.println("{\"passed\":true,\"scope\":\"BACKEND_JAVA_PARSE_HANDOFF_PUBLIC_PROJECTION\",\"databaseWritten\":false}");
    }
}
