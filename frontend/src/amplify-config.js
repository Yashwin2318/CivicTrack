
import { Amplify } from "aws-amplify";

/*
 * CivicTrack Cognito Configuration
 * Uses direct email/password authentication.
 * Hosted UI / OAuth is not required.
 */

const userPoolId = import.meta.env.VITE_COGNITO_USER_POOL_ID;
const userPoolClientId = import.meta.env.VITE_COGNITO_CLIENT_ID;

export function configureAmplify() {
    if (!userPoolId || !userPoolClientId) {
        console.info(
            "Cognito configuration is missing. Set VITE_COGNITO_USER_POOL_ID and VITE_COGNITO_CLIENT_ID."
        );
        return false;
    }

    Amplify.configure({
        Auth: {
            Cognito: {
                userPoolId,
                userPoolClientId,
                loginWith: {
                    email: true,
                },
            },
        },
    });

    return true;
}
