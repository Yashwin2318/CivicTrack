import boto3


cognito = boto3.client("cognito-idp")


def lambda_handler(event, context):
    """
    Add self-registered, confirmed users to the Citizen group.
    Admin-created users are not automatically granted a role.
    """
    trigger_source = event.get("triggerSource", "")

    if trigger_source != "PostConfirmation_ConfirmSignUp":
        return event

    user_pool_id = event.get("userPoolId")
    username = event.get("userName")

    if not user_pool_id or not username:
        raise ValueError("Cognito confirmation event is missing pool ID or username")

    cognito.admin_add_user_to_group(
        UserPoolId=user_pool_id,
        Username=username,
        GroupName="Citizen",
    )

    return event
